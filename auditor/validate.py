"""Valida la herramienta contra una "verdad" etiquetada A MANO.

Uso:
    python -m auditor.validate output/demo/results.csv samples/ground_truth.csv

ground_truth.csv tiene columnas: Repositorio, Real, Comentario
    Real = Sí  -> el repo tiene pruebas unitarias reales
    Real = No  -> no las tiene (o solo tiene pruebas de plantilla)
    Real = No aplica -> repo sin código (docs, infraestructura)

Métricas (piensa en "Sí" como lo que queremos encontrar):
    Precisión = de los que la herramienta dijo "Sí", ¿cuántos realmente lo son?
                (baja si hay FALSOS POSITIVOS: dice Sí y en realidad no hay pruebas)
    Recall    = de los que realmente tienen pruebas, ¿cuántos encontró?
                (baja si hay FALSOS NEGATIVOS: dice No y en realidad sí hay pruebas)
"""
from __future__ import annotations

import csv
import sys
from collections import Counter

YES, NO = "Sí", "No"


def read_csv(path: str) -> list[dict]:
    with open(path, newline="", encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


def evaluate(predicted: dict[str, str], truth: dict[str, str]) -> dict:
    """predicted/truth: repo -> veredicto. Devuelve conteos y métricas."""
    matrix = Counter()
    errors: list[tuple[str, str, str]] = []
    for repo, real in truth.items():
        pred = predicted.get(repo, "(no analizado)")
        matrix[(real, pred)] += 1
        if pred != real:
            errors.append((repo, real, pred))

    tp = matrix[(YES, YES)]
    fp = matrix[(NO, YES)]
    fn = matrix[(YES, NO)]
    tn = matrix[(NO, NO)]
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    total = sum(matrix.values())
    correct = total - len(errors)
    return {"tp": tp, "fp": fp, "fn": fn, "tn": tn, "precision": precision, "recall": recall,
            "accuracy": correct / total if total else None, "total": total, "errors": errors}


def _pct(x):
    return "n/d" if x is None else f"{100 * x:.0f}%"


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    if len(argv) != 2:
        print(__doc__)
        return 2
    predicted = {r["Repositorio"]: r["¿Tiene pruebas unitarias?"] for r in read_csv(argv[0])}
    truth = {r["Repositorio"]: r["Real"] for r in read_csv(argv[1])}
    res = evaluate(predicted, truth)

    print(f"Repos comparados: {res['total']}")
    print(f"  Verdaderos positivos (dijo Sí y es Sí):  {res['tp']}")
    print(f"  Verdaderos negativos (dijo No y es No):  {res['tn']}")
    print(f"  Falsos positivos     (dijo Sí y es No):  {res['fp']}")
    print(f"  Falsos negativos     (dijo No y es Sí):  {res['fn']}")
    print(f"Precisión: {_pct(res['precision'])} · Recall: {_pct(res['recall'])} · "
          f"Exactitud global: {_pct(res['accuracy'])}")
    if res["errors"]:
        print("\nDesacuerdos (revísalos: ¿falla la herramienta o la etiqueta?):")
        for repo, real, pred in res["errors"]:
            print(f"  - {repo}: real={real} · herramienta={pred}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
