"""Carga y valida rules.yaml, y compila las expresiones regulares una sola vez.

Por qué compilar: una regex "compilada" se prepara una vez y luego se reutiliza
miles de veces. Si analizamos 2000 repos, compilar en cada uso sería un desperdicio.
"""
from __future__ import annotations

import fnmatch
import posixpath
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml


class RulesError(Exception):
    """rules.yaml tiene un error (falta un campo, una regex inválida...)."""


def _compile(patterns, flags=0, where="") -> list[re.Pattern]:
    compiled = []
    for pattern in patterns or []:
        try:
            compiled.append(re.compile(pattern, flags))
        except re.error as exc:
            raise RulesError(f"Regex inválida en {where}: {pattern!r} ({exc})") from exc
    return compiled


def _any(patterns: list[re.Pattern], text: str) -> bool:
    return any(p.search(text) for p in patterns)


@dataclass
class Ecosystem:
    """Reglas ya compiladas de UNA tecnología (Java, Python, Go...)."""
    key: str
    name: str
    manifests: list[str]
    extensions: tuple[str, ...]
    fallback_by_extension: bool
    flavors: list[tuple[str, re.Pattern]]
    framework_required: bool
    framework_res: list[re.Pattern]
    test_path_res: list[re.Pattern]
    test_exclude_res: list[re.Pattern]
    case_res: list[re.Pattern]
    trivial_res: list[re.Pattern]
    ci_res: list[re.Pattern]
    ci_skip_res: list[re.Pattern]

    def is_manifest(self, path: str) -> bool:
        base = posixpath.basename(path)
        return any(fnmatch.fnmatchcase(base, m) for m in self.manifests)

    def is_source(self, path: str) -> bool:
        return bool(self.extensions) and path.lower().endswith(self.extensions)

    def is_test_file(self, path: str) -> bool:
        return _any(self.test_path_res, path) and not _any(self.test_exclude_res, path)


@dataclass
class Rules:
    ecosystems: list[Ecosystem]
    ignore_re: re.Pattern
    ci_file_res: list[re.Pattern]
    unsupported_ext: dict[str, str]
    max_manifests: int = 6
    max_ci_files: int = 6
    sample_test_files: int = 5
    max_file_bytes: int = 200_000
    raw: dict = field(default_factory=dict, repr=False)


def load_rules(path: str | Path) -> Rules:
    """Lee el YAML y devuelve las reglas listas para usar."""
    with open(path, encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict) or "ecosystems" not in data:
        raise RulesError("rules.yaml debe tener la sección 'ecosystems'")

    settings = data.get("settings", {})
    ecosystems: list[Ecosystem] = []
    for key, eco in data["ecosystems"].items():
        where = f"ecosystems.{key}"
        for required in ("name", "manifests", "extensions", "test_paths", "case_patterns", "ci_patterns"):
            if required not in eco:
                raise RulesError(f"Falta el campo '{required}' en {where}")
        ecosystems.append(Ecosystem(
            key=key,
            name=eco["name"],
            manifests=list(eco["manifests"]),
            extensions=tuple(e.lower() for e in eco["extensions"]),
            fallback_by_extension=bool(eco.get("fallback_by_extension", False)),
            flavors=[(name, re.compile(p, re.I)) for name, p in (eco.get("flavors") or {}).items()],
            framework_required=bool(eco.get("framework_required", True)),
            framework_res=_compile(eco.get("framework_patterns"), re.I, f"{where}.framework_patterns"),
            test_path_res=_compile(eco["test_paths"], 0, f"{where}.test_paths"),
            test_exclude_res=_compile(eco.get("test_exclude_paths"), 0, f"{where}.test_exclude_paths"),
            case_res=_compile(eco["case_patterns"], re.M, f"{where}.case_patterns"),
            trivial_res=_compile(eco.get("trivial_patterns"), re.M, f"{where}.trivial_patterns"),
            ci_res=_compile(eco["ci_patterns"], re.I, f"{where}.ci_patterns"),
            ci_skip_res=_compile(eco.get("ci_skip_patterns"), re.I, f"{where}.ci_skip_patterns"),
        ))

    return Rules(
        ecosystems=ecosystems,
        ignore_re=re.compile(settings.get("ignore_paths", r"(^|/)node_modules/")),
        ci_file_res=_compile(settings.get("ci_files"), 0, "settings.ci_files"),
        unsupported_ext={k.lower(): v for k, v in (data.get("unsupported_extensions") or {}).items()},
        max_manifests=int(settings.get("max_manifests", 6)),
        max_ci_files=int(settings.get("max_ci_files", 6)),
        sample_test_files=int(settings.get("sample_test_files", 5)),
        max_file_bytes=int(settings.get("max_file_bytes", 200_000)),
        raw=data,
    )
