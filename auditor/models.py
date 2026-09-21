"""Modelos de datos: las "cajas" donde guardamos la información que vamos descubriendo.

Usamos dataclasses: son clases pequeñas que solo guardan datos (sin lógica),
lo que hace el resto del código más fácil de leer y de probar.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

# Posibles veredictos para un repositorio (textos que verá el usuario en la tabla)
YES = "Sí"
NO = "No"
NA = "No aplica"          # repo sin código (docs, configuración, infraestructura)
UNK = "Indeterminado"     # tiene código pero de una tecnología que aún no tiene reglas
ERR = "Error"             # no se pudo analizar (sin permisos, red, etc.)

# Niveles de confianza, de mayor a menor
LEVELS = ["Alta", "Media", "Baja"]


class FatalError(Exception):
    """Errores que deben detener TODO el análisis (no solo un repo).

    Ejemplo: token inválido o un límite de la API que tardaría demasiado en liberarse.
    Los demás errores se registran en el repo afectado y el análisis continúa.
    """


@dataclass(frozen=True)
class FileEntry:
    """Un archivo dentro de un repo: su ruta y su 'sha' (huella única de su contenido)."""
    path: str
    sha: str


@dataclass
class Tree:
    """El árbol completo de archivos de un repo (sin descargar el contenido)."""
    files: list[FileEntry]
    truncated: bool = False   # True si el repo era tan grande que GitHub cortó la lista
    sha: str = ""


@dataclass
class Finding:
    """Resultado para UNA tecnología dentro de un repo (un repo puede tener varias)."""
    ecosystem: str            # nombre base, p.ej. "Java"
    technology: str           # nombre mostrado, p.ej. "Java (Spring Boot)"
    verdict: str              # Sí / No
    confidence: str           # Alta / Media / Baja
    declared: bool            # ¿el framework de pruebas está declarado en las dependencias?
    test_files: int           # cuántos archivos de prueba se encontraron
    cases_real: int | None    # casos de prueba "reales" vistos en la muestra (None = no se leyó)
    ci_runs_tests: bool       # ¿el pipeline de CI ejecuta las pruebas?
    evidence: list[str] = field(default_factory=list)


@dataclass
class RepoResult:
    """Resultado final para un repo (lo que aparece como una fila de la tabla)."""
    repo: str
    technologies: list[str]
    verdict: str
    confidence: str
    evidence: str
    findings: list[Finding] = field(default_factory=list)
    truncated: bool = False

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "RepoResult":
        data = dict(data)
        data["findings"] = [Finding(**f) for f in data.get("findings", [])]
        return cls(**data)
