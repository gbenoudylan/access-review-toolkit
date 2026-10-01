"""Non-régression : compute_control_coverage ne doit jamais planter quand
aucun compte n'est actif (ou que le jeu de données est vide).

Bug d'origine (dashboard, Control Coverage) :
TypeError: operation 'rand_' not supported for dtype 'str' with dtype 'object'
-> DataFrame.apply(..., axis=1) renvoie un DataFrame vide sur un DataFrame
vide, et `Series & ~DataFrame` échoue.
"""
import sys
import warnings
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from analysis.access_review import analyze_access
from reporting.export import (
    _accepted_mask, compute_control_coverage, _compute_comparison_stats,
)


def _raw(statuses):
    return pd.DataFrame({
        "username": [f"u{i}" for i in range(len(statuses))],
        "system": "BSS",
        "account_status": statuses,
        "last_login_date": "2026-09-20",
        "password_last_set": "2026-08-01",
        "role": "user",
    })


def test_accepted_mask_always_returns_bool_series():
    empty = pd.DataFrame({"username": pd.Series([], dtype="str")})
    m = _accepted_mask(empty, "is_dormant")
    assert isinstance(m, pd.Series) and len(m) == 0 and m.dtype == bool
    full = pd.DataFrame({"username": ["a", "b"],
                         "accepted_finding_keys": [["is_dormant"], []]})
    assert _accepted_mask(full, "is_dormant").tolist() == [True, False]
    assert _accepted_mask(full, "is_orphaned_account").tolist() == [False, False]


def test_coverage_with_no_active_account():
    df = analyze_access(_raw(["Disabled", "Disabled", "Terminated"]))
    assert not df["is_active_for_audit"].any()
    rows = compute_control_coverage(df, {})
    assert len(rows) == 19  # contrôles 1..19


def test_coverage_with_empty_dataframe():
    df = analyze_access(_raw(["Active"])).iloc[0:0]
    rows = compute_control_coverage(df, {})
    assert len(rows) == 19


def test_coverage_with_accepted_keys_column_and_no_active():
    df = analyze_access(_raw(["Disabled", "Disabled"]))
    df["accepted_finding_keys"] = [[] for _ in range(len(df))]
    assert len(compute_control_coverage(df, {})) == 19


def test_coverage_with_numeric_statuses_and_comparison():
    cur = analyze_access(_raw([0, 0, -1]))
    prev = analyze_access(_raw([0, -1]))
    stats = _compute_comparison_stats(cur, prev)
    with warnings.catch_warnings():
        warnings.simplefilter("error", FutureWarning)
        assert len(compute_control_coverage(cur, stats)) == 19
