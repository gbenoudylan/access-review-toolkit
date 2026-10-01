"""Non-régression : section « a. Summary of the review » avec statuts numériques.

Bug d'origine : les statuts entiers (1, 0, -1, 2) étaient convertis en chaînes
pour l'affichage mais cherchés dans un index entier -> 0 partout, alors que la
ligne TOTAL (len(df)) restait juste. Le tableau était incohérent.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
from docx import Document

from reporting.export import _status_breakdown_rows, generate_word_report, generate_pdf_report


def _df(statuses, prefix="u"):
    return pd.DataFrame({
        "username": [f"{prefix}{i}" for i in range(len(statuses))],
        "system": "BSS",
        "account_status": statuses,
    })


def _check_sums(rows):
    body, total = rows[1:-1], rows[-1]
    for col in range(1, len(total)):
        if total[col].startswith(("+", "-")):
            assert sum(int(r[col]) for r in body) == int(total[col])
        else:
            assert sum(int(r[col]) for r in body) == int(total[col]), (col, rows)


def test_numeric_statuses_with_previous():
    cur = _df([1, 1, 1, 0, 0, -1, 2])
    prev = _df([1, 1, 0, -1, -1, 2, 2, 2], prefix="p")
    rows = _status_breakdown_rows(cur, prev)
    d = {r[0]: r[1:] for r in rows[1:-1]}
    assert d["1"] == ["2", "3", "+1"]
    assert d["0"] == ["1", "2", "+1"]
    assert d["-1"] == ["2", "1", "-1"]
    assert d["2"] == ["3", "1", "-2"]
    assert rows[-1] == ["TOTAL", "8", "7", "-1"]
    _check_sums(rows)


def test_numeric_statuses_without_previous():
    rows = _status_breakdown_rows(_df([1, 1, 0, -1, 2]), None)
    assert dict((r[0], r[1]) for r in rows[1:-1]) == {"1": "2", "0": "1", "-1": "1", "2": "1"}
    _check_sums(rows)


def test_mixed_int_and_str_and_missing_statuses_sum_to_total():
    cur = _df([1, "1", " Active ", None, float("nan"), "Active"])
    rows = _status_breakdown_rows(cur, None)
    d = {r[0]: int(r[1]) for r in rows[1:-1]}
    assert d["1"] == 2 and d["Active"] == 2 and d["Unknown"] == 2
    _check_sums(rows)


def test_reports_render_numeric_statuses(tmp_path):
    cur = _df([1, 1, 0, -1, 2])
    prev = _df([1, 0, 0, -1, -1, 2], prefix="p")
    docx_path = generate_word_report(cur, tmp_path / "r.docx", previous_df=prev)
    generate_pdf_report(cur, tmp_path / "r.pdf", previous_df=prev)
    tables = Document(str(docx_path)).tables
    summary = next(t for t in tables if t.rows[0].cells[0].text == "Type of Users")
    got = {r.cells[0].text: [c.text for c in r.cells[1:]] for r in summary.rows[1:]}
    assert got["1"] == ["1", "2", "+1"]
    assert got["TOTAL"] == ["6", "5", "-1"]
    ind = next(t for t in tables if t.rows[0].cells[0].text == "Indicator")
    assert all(r.cells[1].text.lstrip("-").isdigit() for r in ind.rows[1:]), "pas de 'None' dans le Word"
