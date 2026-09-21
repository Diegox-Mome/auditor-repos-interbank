"""Genera la tabla final, el detalle por tecnología y un resumen con insights."""
from __future__ import annotations

import csv
from collections import Counter, defaultdict
from pathlib import Path

from .models import ERR, NA, NO, UNK, YES, RepoResult

COLUMNS = ["Repositorio", "Tecnología", "¿Tiene pruebas unitarias?", "Confianza", "Evidencia"]


def _repo_row(r: RepoResult) -> list[str]:
    return [r.repo, " + ".join(r.technologies) or "-", r.verdict, r.confidence, r.evidence]


def write_reports(results: list[RepoResult], out_dir: str | Path) -> dict[str, Path]:
    """Escribe results.csv (la tabla pedida), detail.csv y summary.md. Devuelve las rutas."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    results = sorted(results, key=lambda r: r.repo.lower())

    # utf-8-sig: Excel muestra bien tildes y "Sí" al abrir el CSV.
    main_path = out / "results.csv"
    with open(main_path, "w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.writer(fh)
        writer.writerow(COLUMNS)
        writer.writerows(_repo_row(r) for r in results)

    detail_path = out / "detail.csv"
    with open(detail_path, "w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.writer(fh)
        writer.writerow(["Repositorio", "Tecnología", "Veredicto", "Confianza",
                         "Framework declarado", "Archivos de prueba", "Casos reales (muestra)",
                         "CI ejecuta pruebas", "Evidencia"])
        for r in results:
            for f in r.findings:
                writer.writerow([r.repo, f.technology, f.verdict, f.confidence,
                                 "sí" if f.declared else "no", f.test_files,
                                 "" if f.cases_real is None else f.cases_real,
                                 "sí" if f.ci_runs_tests else "no", "; ".join(f.evidence)])

    summary_path = out / "summary.md"
    summary_path.write_text(summary_text(results), encoding="utf-8")
    return {"results": main_path, "detail": detail_path, "summary": summary_path}


def summary_text(results: list[RepoResult]) -> str:
    total = len(results)
    counts = Counter(r.verdict for r in results)
    decided = counts[YES] + counts[NO]
    lines = ["# Resumen del análisis", "", f"Repositorios analizados: **{total}**", ""]
    lines += ["| Resultado | Repos | % |", "|---|---:|---:|"]
    for verdict in (YES, NO, NA, UNK, ERR):
        if counts[verdict]:
            lines.append(f"| {verdict} | {counts[verdict]} | {100 * counts[verdict] / total:.0f}% |")
    if decided:
        lines += ["", f"**Cobertura de pruebas** (repos con código soportado): "
                      f"{counts[YES]} de {decided} = {100 * counts[YES] / decided:.0f}%"]

    by_tech: dict[str, Counter] = defaultdict(Counter)
    for r in results:
        for f in r.findings:
            by_tech[f.ecosystem][f.verdict] += 1
    if by_tech:
        lines += ["", "## Por tecnología", "", "| Tecnología | Con pruebas | Sin pruebas | % con pruebas |",
                  "|---|---:|---:|---:|"]
        for tech, c in sorted(by_tech.items()):
            n = c[YES] + c[NO]
            lines.append(f"| {tech} | {c[YES]} | {c[NO]} | {100 * c[YES] / n:.0f}% |")

    low = [r.repo for r in results if r.verdict in (YES, NO) and r.confidence == "Baja"]
    unk = [f"{r.repo} ({', '.join(r.technologies)})" for r in results if r.verdict == UNK]
    err = [f"{r.repo}: {r.evidence}" for r in results if r.verdict == ERR]
    if low:
        lines += ["", "## Para revisión manual (confianza baja)", ""] + [f"- {x}" for x in low]
    if unk:
        lines += ["", "## Tecnologías sin reglas todavía", ""] + [f"- {x}" for x in unk]
    if err:
        lines += ["", "## Errores", ""] + [f"- {x}" for x in err]
    return "\n".join(lines) + "\n"


def console_table(results: list[RepoResult], evidence_width: int = 60) -> str:
    rows = [COLUMNS[:4] + ["Evidencia"]]
    for r in sorted(results, key=lambda r: r.repo.lower()):
        ev = r.evidence if len(r.evidence) <= evidence_width else r.evidence[: evidence_width - 1] + "…"
        rows.append(_repo_row(r)[:4] + [ev])
    widths = [max(len(row[i]) for row in rows) for i in range(5)]
    fmt = lambda row: "  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row))  # noqa: E731
    return "\n".join([fmt(rows[0]), "  ".join("-" * w for w in widths)] + [fmt(r) for r in rows[1:]])
