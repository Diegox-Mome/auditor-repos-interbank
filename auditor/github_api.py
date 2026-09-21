"""Acceso a GitHub por su API REST.

Tres problemas reales que resuelve este módulo (y por qué importan a 2000 repos):

1. RATE LIMIT (límite de peticiones): GitHub permite un número máximo de llamadas por hora
   (típicamente 5,000 con un token personal; el valor exacto depende de tu plan/instancia).
   Cuando se acaba, responde 403/429. Aquí leemos las cabeceras y ESPERAMOS hasta que se
   libere, en vez de fallar. Todos los hilos se pausan juntos para no empeorar el bloqueo.

2. CACHÉ CON ETag: cada respuesta trae una "huella" (ETag). Si la próxima vez preguntamos
   "dame el árbol solo si cambió" (cabecera If-None-Match) y no cambió, GitHub responde 304
   y esa llamada NO gasta cuota. Así, la segunda corrida sobre 2000 repos casi no cuesta.

3. CONTENIDO POR SHA: cada archivo tiene un sha (huella de su contenido). Si el sha no
   cambió, el contenido tampoco: lo guardamos en disco y no lo volvemos a descargar jamás.
"""
from __future__ import annotations

import base64
import json
import os
import threading
import time
from pathlib import Path
from urllib.parse import quote

import requests

from .models import FatalError, FileEntry, Tree


class RateLimitError(FatalError):
    """El límite de la API tardaría demasiado en liberarse."""


class AuthError(FatalError):
    """El token es inválido o no tiene permisos."""


class RepoAccessError(Exception):
    """El repo no existe o el token no puede verlo."""


class HttpClient:
    """Cliente HTTP con reintentos y manejo del rate limit compartido entre hilos."""

    def __init__(self, token: str | None = None, api_url: str = "https://api.github.com",
                 max_wait: int = 900, timeout: int = 30, session_factory=requests.Session,
                 sleep=time.sleep, now=time.time):
        self.token = token
        self.api_url = api_url.rstrip("/")
        self.max_wait = max_wait
        self.timeout = timeout
        self._session_factory = session_factory
        self._sleep = sleep
        self._now = now
        self._local = threading.local()      # una sesión HTTP por hilo (más seguro)
        self._lock = threading.Lock()
        self._paused_until = 0.0             # si es > ahora, todos los hilos esperan

    def _session(self):
        if not hasattr(self._local, "session"):
            self._local.session = self._session_factory()
        return self._local.session

    def _headers(self, url: str, extra: dict | None) -> dict:
        headers = {"Accept": "application/vnd.github+json", "User-Agent": "repo-test-auditor"}
        if self.token:
            # El token se envía solo a GitHub (API o raw), nunca a otros dominios.
            scheme = "Bearer" if url.startswith(self.api_url) else "token"
            headers["Authorization"] = f"{scheme} {self.token}"
        headers.update(extra or {})
        return headers

    def _wait_if_paused(self):
        with self._lock:
            delay = self._paused_until - self._now()
        if delay > 0:
            self._sleep(delay)

    def _pause(self, seconds: float):
        with self._lock:
            self._paused_until = max(self._paused_until, self._now() + seconds)

    def _wait_needed(self, resp) -> float | None:
        """¿Esta respuesta 403/429 es por rate limit? Si sí, ¿cuántos segundos esperar?"""
        retry_after = resp.headers.get("Retry-After")
        if retry_after:
            return float(retry_after) + 1
        if resp.headers.get("X-RateLimit-Remaining") == "0":
            reset = float(resp.headers.get("X-RateLimit-Reset", "0"))
            return max(0.0, reset - self._now()) + 1
        if "rate limit" in (getattr(resp, "text", "") or "").lower():
            return 60.0      # límite "secundario" (demasiadas peticiones seguidas)
        return None

    def get(self, url: str, params: dict | None = None, headers: dict | None = None):
        attempts = 0
        while True:
            self._wait_if_paused()
            resp = self._session().get(url, params=params, timeout=self.timeout,
                                       headers=self._headers(url, headers))
            if resp.status_code == 401:
                raise AuthError("Token inválido o vencido (HTTP 401). Revisa GITHUB_TOKEN.")
            if resp.status_code in (403, 429):
                wait = self._wait_needed(resp)
                if wait is not None:
                    if wait > self.max_wait:
                        raise RateLimitError(
                            f"Límite de la API alcanzado; se libera en ~{int(wait)} s "
                            f"(máximo configurado: {self.max_wait} s). "
                            "Define GITHUB_TOKEN (sube el límite de 60 a 5,000 por hora) "
                            "o usa --backend git."
                        )
                    self._pause(wait)
                    attempts += 1
                    if attempts > 5:
                        raise RateLimitError("Demasiados reintentos por rate limit.")
                    continue
            if resp.status_code >= 500 and attempts < 3:
                self._sleep(2 ** attempts)
                attempts += 1
                continue
            return resp


def list_repos(http: HttpClient, owner: str, include_archived: bool = False,
               include_forks: bool = False) -> list[str]:
    """Lista los repos de una organización (o usuario), paginando de 100 en 100."""
    for kind, repo_type in (("orgs", "all"), ("users", "owner")):
        url = f"{http.api_url}/{kind}/{owner}/repos"
        params = {"per_page": 100, "type": repo_type}
        resp = http.get(url, params=params)
        if resp.status_code == 404:
            continue
        names: list[str] = []
        while True:
            if resp.status_code != 200:
                raise RuntimeError(f"No se pudo listar repos de {owner}: HTTP {resp.status_code}")
            for repo in resp.json():
                if repo.get("archived") and not include_archived:
                    continue
                if repo.get("fork") and not include_forks:
                    continue
                names.append(repo["full_name"])
            next_url = resp.links.get("next", {}).get("url")
            if not next_url:
                return names
            resp = http.get(next_url)
    raise RuntimeError(f"No existe la organización/usuario '{owner}'.")


class ApiBackend:
    """Obtiene árboles y archivos vía la API de GitHub (+ raw.githubusercontent.com)."""

    def __init__(self, http: HttpClient, cache_dir: str | Path = ".cache",
                 raw_url: str | None = None, max_file_bytes: int = 200_000):
        self.http = http
        self.api_url = http.api_url
        # En github.com bajamos archivos desde "raw" (no gasta cuota de la API REST).
        # En GitHub Enterprise Server no existe ese dominio: usamos la API de blobs.
        if raw_url is None and self.api_url == "https://api.github.com":
            raw_url = "https://raw.githubusercontent.com"
        self.raw_url = raw_url.rstrip("/") if raw_url else None
        self.cache = Path(cache_dir)
        (self.cache / "trees").mkdir(parents=True, exist_ok=True)
        (self.cache / "blobs").mkdir(parents=True, exist_ok=True)
        self.max_file_bytes = max_file_bytes

    # ---- árbol de archivos --------------------------------------------------
    def get_tree(self, full_name: str) -> Tree:
        cache_file = self.cache / "trees" / (full_name.replace("/", "__") + ".json")
        cached = self._load_json(cache_file)

        resp = self._fetch_tree(full_name, "HEAD", cached)
        if resp.status_code == 404:
            # "HEAD" no resolvió: pedimos la rama por defecto explícitamente.
            branch = self._default_branch(full_name)
            resp = self._fetch_tree(full_name, branch, cached)

        if resp.status_code == 304 and cached:
            return self._tree_from_cache(cached)
        if resp.status_code == 409:              # repositorio vacío
            return Tree([], False, "")
        if resp.status_code == 404:
            raise RepoAccessError("no existe o el token no tiene acceso")
        if resp.status_code != 200:
            raise RuntimeError(f"HTTP {resp.status_code} al leer el árbol")

        data = resp.json()
        files = [[e["path"], e["sha"]] for e in data.get("tree", []) if e.get("type") == "blob"]
        record = {"etag": resp.headers.get("ETag"), "files": files,
                  "truncated": bool(data.get("truncated")), "sha": data.get("sha", "")}
        self._save_json(cache_file, record)
        return self._tree_from_cache(record)

    def _fetch_tree(self, full_name: str, ref: str, cached: dict | None):
        headers = {"If-None-Match": cached["etag"]} if cached and cached.get("etag") else None
        return self.http.get(f"{self.api_url}/repos/{full_name}/git/trees/{quote(ref, safe='/')}",
                             params={"recursive": "1"}, headers=headers)

    def _default_branch(self, full_name: str) -> str:
        resp = self.http.get(f"{self.api_url}/repos/{full_name}")
        if resp.status_code != 200:
            raise RepoAccessError("no existe o el token no tiene acceso")
        return resp.json().get("default_branch", "main")

    @staticmethod
    def _tree_from_cache(record: dict) -> Tree:
        return Tree([FileEntry(p, s) for p, s in record["files"]],
                    record.get("truncated", False), record.get("sha", ""))

    # ---- contenido de archivos ---------------------------------------------
    def read_file(self, full_name: str, entry: FileEntry) -> str | None:
        blob_file = self.cache / "blobs" / entry.sha
        if blob_file.exists():
            return blob_file.read_text(encoding="utf-8", errors="replace")

        if self.raw_url:
            url = f"{self.raw_url}/{full_name}/HEAD/{quote(entry.path)}"
            resp = self.http.get(url)
            if resp.status_code != 200:
                return None
            data = resp.content
        else:
            resp = self.http.get(f"{self.api_url}/repos/{full_name}/git/blobs/{entry.sha}")
            if resp.status_code != 200:
                return None
            payload = resp.json()
            if payload.get("encoding") != "base64":
                return None
            data = base64.b64decode(payload["content"])

        text = data[: self.max_file_bytes].decode("utf-8", errors="replace")
        # Escritura atómica con nombre temporal único por hilo (evita lecturas a medias).
        tmp = blob_file.with_name(f"{entry.sha}.{threading.get_ident()}.tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, blob_file)
        return text

    def close(self, full_name: str) -> None:
        """Nada que limpiar en este backend (el del modo git sí borra su carpeta temporal)."""

    # ---- utilidades de caché -------------------------------------------------
    @staticmethod
    def _load_json(path: Path):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    @staticmethod
    def _save_json(path: Path, data) -> None:
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        os.replace(tmp, path)     # reemplazo atómico: nunca queda un archivo a medias
