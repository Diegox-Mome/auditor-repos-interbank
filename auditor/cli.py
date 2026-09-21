"""Línea de comandos: une todas las piezas.

Ejemplos:
    python -m auditor --repos-file samples/demo_repos.txt --backend git
    python -m auditor --org mi-organizacion --workers 8          (requiere GITHUB_TOKEN)
    python -m auditor --org mi-org --api-url https://ghe.miempresa.com/api/v3   (GitHub Enterprise Server)
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from .engine import Analyzer
from .git_backend import GitBackend
from .github_api import ApiBackend, HttpClient, list_repos
from .models import ERR, FatalError, RepoResult
from .report import console_table, summary_text, write_reports
from .rules import RulesError, load_rules

DEFAULT_RULES = Path(__file__).resolve().parent.parent / "rules.yaml"


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="auditor",
                                description="Detecta qué repositorios de GitHub tienen pruebas unitarias.")
    src = p.add_argument_group("¿Qué repos analizar? (elige uno)")
    src.add_argument("--org", help="organización de GitHub (analiza todos sus repos)")
    src.add_argument("--user", help="usuario de GitHub (analiza sus repos)")
    src.add_argument("--repos-file", help="archivo de texto con un 'owner/repo' por línea")
    p.add_argument("--backend", choices=["api", "git"], default="api",
                   help="api = API REST de GitHub (rápido, usa cuota); git = protocolo git (sin cuota REST)")
    p.add_argument("--api-url", default="https://api.github.com",
                   help="URL de la API (GitHub Enterprise Server: https://HOST/api/v3)")
    p.add_argument("--git-url", default="https://github.com", help="URL base para clonar en modo git")
    p.add_argument("--workers", type=int, default=8, help="repos analizados en paralelo (por defecto 8)")
    p.add_argument("--no-deep", action="store_true",
                   help="no abrir archivos de prueba (más rápido, menos confiable)")
    p.add_argument("--rules", default=str(DEFAULT_RULES), help="archivo de reglas (rules.yaml)")
    p.add_argument("--out", default="output", help="carpeta de resultados")
    p.add_argument("--cache-dir", default=".cache", help="carpeta de caché (modo api)")
    p.add_argument("--limit", type=int, help="analizar solo los primeros N repos (para pruebas)")
    p.add_argument("--include-archived", action="store_true", help="incluir repos archivados")
    p.add_argument("--include-forks", action="store_true", help="incluir forks")
    p.add_argument("--resume", action="store_true", help="saltar repos ya analizados en una corrida previa")
    p.add_argument("--max-wait", type=int, default=900,
                   help="segundos máximos a esperar si se agota la cuota de la API (por defecto 900)")
    return p


def normalize_repo(line: str) -> str | None:
    """Acepta 'owner/repo' o URLs completas de GitHub; devuelve 'owner/repo'."""
    line = line.strip()
    if not line or line.startswith("#"):
        return None
    line = re.sub(r"^https?://[^/]+/", "", line)
    line = re.sub(r"\.git$", "", line).strip("/")
    return line if re.fullmatch(r"[\w.-]+/[\w.-]+", line) else None


def load_done(jsonl: Path) -> dict[str, RepoResult]:
    done: dict[str, RepoResult] = {}
    if jsonl.exists():
        for line in jsonl.read_text(encoding="utf-8").splitlines():
            try:
                res = RepoResult.from_dict(json.loads(line))
            except (ValueError, TypeError):
                continue
            if res.verdict != ERR:       # los errores se reintentan
                done[res.repo] = res
    return done


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not (args.org or args.user or args.repos_file):
        print("Indica qué analizar: --org, --user o --repos-file (usa --help).", file=sys.stderr)
        return 2

    try:
        rules = load_rules(args.rules)
    except (RulesError, OSError) as exc:
        print(f"Error en las reglas: {exc}", file=sys.stderr)
        return 2

    token = os.environ.get("GITHUB_TOKEN")
    http = HttpClient(token=token, api_url=args.api_url, max_wait=args.max_wait)
    if not token:
        print("⚠ Sin GITHUB_TOKEN: la API permite solo ~60 peticiones/hora. "
              "Para pocos repos usa --backend git.", file=sys.stderr)

    # 1) ¿Qué repos?
    try:
        if args.repos_file:
            lines = Path(args.repos_file).read_text(encoding="utf-8").splitlines()
            repos = [r for r in (normalize_repo(x) for x in lines) if r]
        else:
            repos = list_repos(http, args.org or args.user, args.include_archived, args.include_forks)
    except (FatalError, RuntimeError, OSError) as exc:
        print(f"No se pudo obtener la lista de repos: {exc}", file=sys.stderr)
        return 1
    repos = list(dict.fromkeys(repos))                   # quita duplicados conservando el orden
    if args.limit:
        repos = repos[: args.limit]

    # 2) Backend y motor
    if args.backend == "git":
        backend = GitBackend(base_url=args.git_url, token=token)
    else:
        backend = ApiBackend(http, cache_dir=args.cache_dir, max_file_bytes=rules.max_file_bytes)
    analyzer = Analyzer(rules, backend, deep=not args.no_deep)

    # 3) Análisis en paralelo, guardando cada resultado apenas termina (por si se corta)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    jsonl = out_dir / "results.jsonl"
    done = load_done(jsonl) if args.resume else {}
    if not args.resume and jsonl.exists():
        jsonl.unlink()
    pending = [r for r in repos if r not in done]
    results: dict[str, RepoResult] = {r: done[r] for r in repos if r in done}
    print(f"Repos: {len(repos)} (ya hechos: {len(done)}, por analizar: {len(pending)}) — "
          f"backend={args.backend}, workers={args.workers}, deep={not args.no_deep}")

    started = time.time()
    interrupted = False
    with open(jsonl, "a", encoding="utf-8") as sink, ThreadPoolExecutor(args.workers) as pool:
        futures = {pool.submit(analyzer.analyze, r): r for r in pending}
        try:
            for i, future in enumerate(as_completed(futures), 1):
                res = future.result()
                results[res.repo] = res
                sink.write(json.dumps(res.to_dict(), ensure_ascii=False) + "\n")
                sink.flush()
                print(f"[{i}/{len(pending)}] {res.repo:<45} {res.verdict:<13} {res.confidence}", flush=True)
        except FatalError as exc:
            interrupted = True
            print(f"\n✖ Proceso detenido: {exc}", file=sys.stderr)
            pool.shutdown(wait=False, cancel_futures=True)
        except KeyboardInterrupt:
            interrupted = True
            print("\nInterrumpido por el usuario; se guardó lo avanzado.", file=sys.stderr)
            pool.shutdown(wait=False, cancel_futures=True)

    # 4) Reportes
    final = [results[r] for r in repos if r in results]
    paths = write_reports(final, out_dir)
    print("\n" + console_table(final) + "\n")
    print(summary_text(final))
    print(f"Tiempo: {time.time() - started:.1f} s · Archivos: {paths['results']}, {paths['detail']}, {paths['summary']}")
    if interrupted:
        print("Continúa después con --resume para no repetir lo ya hecho.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
