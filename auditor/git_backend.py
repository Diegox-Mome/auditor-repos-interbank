"""Backend alternativo: usa el protocolo git en lugar de la API REST.

Cómo funciona (clon "blobless"):
    git clone --depth 1 --filter=blob:none --no-checkout <url>
  Esto descarga SOLO la estructura (árboles), NO el contenido de los archivos: unos pocos
  KB en vez de todo el repo. Con `git ls-tree` obtenemos la lista completa de archivos, y
  `git cat-file` descarga bajo demanda únicamente los archivos que necesitamos leer.

Por qué existe:
  * No consume la cuota de la API REST (útil si el límite es el cuello de botella).
  * Funciona sin token en repos públicos (ideal para una demo).
  * Funciona igual con GitHub Enterprise Server (solo cambia la URL base).

Compromiso: cada repo requiere lanzar procesos git (más lento por repo que una llamada
REST), pero como se paraleliza y no tiene límite por hora, escala bien.

El motor no distingue entre este backend y el de la API: ambos ofrecen
get_tree(), read_file() y close().
"""
from __future__ import annotations

import base64
import os
import shutil
import subprocess
import tempfile

from .models import FileEntry, Tree


class GitBackend:
    def __init__(self, base_url: str = "https://github.com", token: str | None = None,
                 timeout: int = 180, max_file_bytes: int = 200_000):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_file_bytes = max_file_bytes
        self._dirs: dict[str, str] = {}

        # Entorno para git. GIT_TERMINAL_PROMPT=0: si pide contraseña, falla en vez de quedarse esperando.
        self._env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
        if token:
            # El token viaja en una variable de entorno (no en la línea de comandos, donde
            # otros procesos podrían verlo) como cabecera de autenticación HTTP.
            basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
            self._env.update(GIT_CONFIG_COUNT="1", GIT_CONFIG_KEY_0="http.extraHeader",
                             GIT_CONFIG_VALUE_0=f"Authorization: Basic {basic}")

    def _git(self, args: list[str], cwd: str | None = None) -> subprocess.CompletedProcess:
        return subprocess.run(["git", *args], cwd=cwd, env=self._env, capture_output=True,
                              timeout=self.timeout)

    def get_tree(self, full_name: str) -> Tree:
        workdir = tempfile.mkdtemp(prefix="auditor-")
        self._dirs[full_name] = workdir
        url = f"{self.base_url}/{full_name}.git"
        clone = self._git(["clone", "--quiet", "--depth", "1", "--filter=blob:none",
                           "--no-checkout", url, workdir])
        if clone.returncode != 0:
            raise RuntimeError("git clone falló: " + clone.stderr.decode(errors="replace").strip()[:200])

        ls = self._git(["ls-tree", "-r", "-z", "HEAD"], cwd=workdir)
        if ls.returncode != 0:
            return Tree([], False, "")        # repo vacío (HEAD no existe)

        files = []
        for record in ls.stdout.decode("utf-8", errors="replace").split("\0"):
            if not record:
                continue
            meta, path = record.split("\t", 1)          # "<modo> <tipo> <sha>\t<ruta>"
            _mode, kind, sha = meta.split()
            if kind == "blob":
                files.append(FileEntry(path, sha))
        return Tree(files)

    def read_file(self, full_name: str, entry: FileEntry) -> str | None:
        workdir = self._dirs.get(full_name)
        if not workdir:
            return None
        out = self._git(["cat-file", "blob", entry.sha], cwd=workdir)   # descarga bajo demanda
        if out.returncode != 0:
            return None
        return out.stdout[: self.max_file_bytes].decode("utf-8", errors="replace")

    def close(self, full_name: str) -> None:
        """Borra el clon temporal: 2000 repos no deben llenar el disco."""
        workdir = self._dirs.pop(full_name, None)
        if workdir:
            shutil.rmtree(workdir, ignore_errors=True)
