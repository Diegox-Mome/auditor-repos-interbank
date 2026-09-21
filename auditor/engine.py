"""El motor: dado un repo, decide si tiene pruebas unitarias y con qué evidencia.

Flujo para cada repo:
  1. Pedir el árbol de archivos (lista de rutas, sin contenido).
  2. Detectar qué tecnologías tiene (por sus archivos de proyecto: pom.xml, package.json...).
  3. Por cada tecnología, juntar 3 capas de evidencia:
       a) DECLARADA : el framework de pruebas está en las dependencias.
       b) PRESENTE  : existen archivos de prueba (y tienen casos de prueba reales).
       c) EJECUTADA : el pipeline de CI corre las pruebas.
  4. Combinar las capas en un veredicto (Sí/No) con nivel de confianza.

El motor no sabe nada de GitHub: pide los datos a un "backend" (ver backends).
Eso permite probarlo sin internet con un backend falso.
"""
from __future__ import annotations

import posixpath
from collections import Counter

from .models import (ERR, LEVELS, NA, NO, UNK, YES, FatalError, FileEntry, Finding,
                     RepoResult)
from .rules import Ecosystem, Rules


def looks_like_test(path: str) -> bool:
    """¿El nombre del archivo 'suena' a prueba? (contiene test o spec)"""
    base = posixpath.basename(path).lower()
    return "test" in base or "spec" in base


class Analyzer:
    def __init__(self, rules: Rules, backend, deep: bool = True):
        """deep=True abre algunos archivos de prueba para contar casos reales.
        deep=False solo mira nombres de archivos (más rápido, menos confiable)."""
        self.rules = rules
        self.backend = backend
        self.deep = deep

    # ------------------------------------------------------------------ público
    def analyze(self, full_name: str) -> RepoResult:
        """Analiza un repo. Nunca lanza errores "normales": los convierte en una fila 'Error'."""
        try:
            return self._analyze(full_name)
        except FatalError:
            raise  # estos sí deben detener todo el proceso
        except Exception as exc:  # noqa: BLE001 - un repo con problemas no debe tumbar 2000
            return RepoResult(full_name, [], ERR, "-", f"{type(exc).__name__}: {exc}")
        finally:
            close = getattr(self.backend, "close", None)
            if close:
                close(full_name)

    # ---------------------------------------------------------------- privado
    def _analyze(self, full_name: str) -> RepoResult:
        tree = self.backend.get_tree(full_name)
        files = [f for f in tree.files if not self.rules.ignore_re.search(f.path)]
        if not files:
            return RepoResult(full_name, [], NA, "Alta", "Repositorio vacío o sin archivos relevantes")

        ci_docs = self._read_ci_files(full_name, files)

        findings: list[Finding] = []
        for eco in self.rules.ecosystems:
            finding = self._analyze_ecosystem(full_name, eco, files, ci_docs)
            if finding:
                findings.append(finding)

        if findings:
            return self._aggregate(full_name, findings, tree.truncated)
        return self._no_supported_technology(full_name, files, tree.truncated)

    # ---- lectura de archivos -------------------------------------------------
    def _read(self, full_name: str, entry: FileEntry) -> str | None:
        try:
            return self.backend.read_file(full_name, entry)
        except FatalError:
            raise
        except Exception:  # noqa: BLE001 - si un archivo no se puede leer, seguimos sin él
            return None

    def _read_ci_files(self, full_name: str, files: list[FileEntry]) -> list[tuple[str, str]]:
        ci_files = [f for f in files if any(rx.search(f.path) for rx in self.rules.ci_file_res)]
        ci_files.sort(key=lambda f: (f.path.count("/"), f.path))
        docs = []
        for entry in ci_files[: self.rules.max_ci_files]:
            text = self._read(full_name, entry)
            if text:
                docs.append((entry.path, text))
        return docs

    # ---- análisis de una tecnología -----------------------------------------
    def _analyze_ecosystem(self, full_name: str, eco: Ecosystem, files: list[FileEntry],
                           ci_docs: list[tuple[str, str]]) -> Finding | None:
        manifests = [f for f in files if eco.is_manifest(f.path)]
        has_source = any(eco.is_source(f.path) for f in files)

        # ¿Está presente esta tecnología en el repo?
        if manifests:
            if eco.extensions and not has_source:
                return None      # p.ej. un package.json de herramientas en un repo de Python
        elif not (eco.fallback_by_extension and has_source):
            return None

        # Capa (a) DECLARADA: leer manifiestos y buscar el framework de pruebas
        manifest_text = "\n".join(
            t for t in (self._read(full_name, m) for m in self._pick_manifests(manifests)) if t
        )
        framework_hit = self._first_match(eco.framework_res, manifest_text)
        declared = framework_hit is not None
        flavor = next((name for name, rx in eco.flavors if rx.search(manifest_text)), None)
        technology = flavor or eco.name

        # Capa (c) EJECUTADA: ¿el CI corre pruebas de esta tecnología?
        ci_file = self._ci_hit(eco, ci_docs)

        # Capa (b) PRESENTE: archivos de prueba
        test_files = [f for f in files if eco.is_test_file(f.path)]
        evidence: list[str] = []
        if declared:
            evidence.append(f"framework declarado ({framework_hit})")
        if ci_file:
            evidence.append(f"CI ejecuta pruebas ({ci_file})")

        if not test_files:
            evidence.insert(0, "sin archivos de prueba" + (" aunque hay framework/CI configurado"
                                                          if (declared or ci_file) else ""))
            confidence = "Alta" if not (declared or ci_file) else "Media"
            return Finding(eco.name, technology, NO, confidence, declared, 0, None,
                           bool(ci_file), evidence)

        example = min(test_files, key=lambda f: (not looks_like_test(f.path), f.path.count("/"), f.path))
        evidence.insert(0, f"{len(test_files)} archivo(s) de prueba (p.ej. {example.path})")

        cases_real: int | None = None
        if self.deep:
            cases_real, verdict_note, all_inspected = self._count_cases(full_name, eco, test_files)
            if cases_real is not None:
                evidence.append(verdict_note)
                if cases_real == 0 and all_inspected:
                    # Hay archivos, pero solo casos de plantilla o ninguno: no cuenta como pruebas.
                    return Finding(eco.name, technology, NO, "Media", declared, len(test_files),
                                   0, bool(ci_file), evidence)

        # Veredicto Sí. Confianza = cuántas señales independientes coinciden.
        applicable = 1 + (1 if self.deep and cases_real is not None else 0) \
                       + (1 if eco.framework_required else 0)
        points = (1 if ci_file else 0) \
               + (1 if cases_real else 0) \
               + (1 if (eco.framework_required and declared) else 0)
        confidence = LEVELS[min(applicable - points, 2)]
        return Finding(eco.name, technology, YES, confidence, declared, len(test_files),
                       cases_real, bool(ci_file), evidence)

    def _pick_manifests(self, manifests: list[FileEntry]) -> list[FileEntry]:
        """Elige cuáles leer: los más cercanos a la raíz y los que parecen de pruebas."""
        limit = self.rules.max_manifests
        by_depth = sorted(manifests, key=lambda f: (f.path.count("/"), f.path))
        picked = by_depth[: max(1, limit // 2)]
        for f in by_depth:
            if len(picked) >= limit:
                break
            if f not in picked and "test" in f.path.lower():
                picked.append(f)
        for f in by_depth:
            if len(picked) >= limit:
                break
            if f not in picked:
                picked.append(f)
        return picked

    @staticmethod
    def _first_match(patterns, text: str) -> str | None:
        for rx in patterns:
            m = rx.search(text)
            if m:
                return "".join(ch for ch in m.group(0) if ch.isalnum() or ch in "@/.-_")
        return None

    @staticmethod
    def _ci_hit(eco: Ecosystem, ci_docs: list[tuple[str, str]]) -> str | None:
        """Devuelve el archivo de CI que ejecuta pruebas (o None)."""
        for path, text in ci_docs:
            for line in text.splitlines():
                stripped = line.strip()
                if not stripped or stripped.startswith(("#", "//")):
                    continue  # línea vacía o comentada
                if any(rx.search(stripped) for rx in eco.ci_res) and \
                        not any(rx.search(stripped) for rx in eco.ci_skip_res):
                    return path
        return None

    def _count_cases(self, full_name: str, eco: Ecosystem, test_files: list[FileEntry]):
        """Abre una muestra de archivos de prueba y cuenta casos (descartando los de plantilla).

        Devuelve (casos_reales | None, nota, se_inspeccionaron_todos_los_archivos).
        """
        sample = self._sample(test_files, self.rules.sample_test_files)
        total = trivial = read_ok = 0
        for entry in sample:
            text = self._read(full_name, entry)
            if text is None:
                continue
            read_ok += 1
            total += sum(len(rx.findall(text)) for rx in eco.case_res)
            trivial += sum(len(rx.findall(text)) for rx in eco.trivial_res)
        if read_ok == 0:
            return None, "", False
        real = max(0, total - trivial)
        all_inspected = len(sample) == len(test_files) and read_ok == len(sample)
        if real == 0:
            note = ("solo casos de plantilla generados automáticamente" if trivial
                    else "los archivos de prueba no contienen casos")
        else:
            note = f"{real} caso(s) de prueba reales en {read_ok} archivo(s) revisado(s)"
            if trivial:
                note += f" (+{trivial} de plantilla ignorados)"
        return real, note, all_inspected

    @staticmethod
    def _sample(files: list[FileEntry], k: int) -> list[FileEntry]:
        """Muestra repartida de archivos, prefiriendo los que 'se llaman' test/spec."""
        pool = sorted(files, key=lambda f: (not looks_like_test(f.path), f.path))
        likely = [f for f in pool if looks_like_test(f.path)]
        pool = likely or pool
        if len(pool) <= k:
            return pool
        step = len(pool) / k
        return [pool[int(i * step)] for i in range(k)]

    # ---- combinar resultados -------------------------------------------------
    def _aggregate(self, full_name: str, findings: list[Finding], truncated: bool) -> RepoResult:
        """Un repo con varias tecnologías: si CUALQUIERA tiene pruebas, el repo cuenta como 'Sí'."""
        def rank(f: Finding):
            return (0 if f.verdict == YES else 1, LEVELS.index(f.confidence))

        best = min(findings, key=rank)
        technologies = [f.technology for f in findings]
        if len(findings) == 1:
            evidence = "; ".join(best.evidence)
        else:
            evidence = " | ".join(f"{f.technology}: {'; '.join(f.evidence)}" for f in findings)

        confidence = best.confidence
        if best.verdict == NO:
            # Para afirmar "No" en un repo con varias tecnologías, TODAS deben ser "No":
            # la confianza global es la del eslabón más débil.
            confidence = max((f.confidence for f in findings), key=LEVELS.index)
        if truncated:
            evidence += " | árbol truncado por tamaño del repo: resultado parcial"
            if best.verdict == NO:
                confidence = "Baja"
        return RepoResult(full_name, technologies, best.verdict, confidence, evidence,
                          findings, truncated)

    def _no_supported_technology(self, full_name: str, files: list[FileEntry],
                                 truncated: bool) -> RepoResult:
        counts = Counter()
        for f in files:
            ext = posixpath.splitext(f.path.lower())[1]
            if ext in self.rules.unsupported_ext:
                counts[self.rules.unsupported_ext[ext]] += 1
        if counts:
            language, _ = counts.most_common(1)[0]
            return RepoResult(full_name, [language], UNK, "-",
                              f"Código en {language}: aún no hay reglas (agregarlas en rules.yaml)",
                              truncated=truncated)
        return RepoResult(full_name, [], NA, "Alta",
                          "Sin código fuente reconocible (documentación, configuración o infraestructura)",
                          truncated=truncated)
