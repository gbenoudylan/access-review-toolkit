"""Non-régression : un statut numérique négatif ('-1') ne doit jamais être
confondu avec sa valeur positive ('1').

Bug d'origine : _normalize / _tokenize_status_value remplaçaient le '-' par
un séparateur, donc '-1' devenait '1'. Mapper '-1' en 'disabled' désactivait
alors tous les comptes à '1', plus aucun compte n'était actif, et le
dashboard (Control Coverage) plantait.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from analysis.access_review import (
    _is_active_account, _tokenize_status_value, analyze_access,
)
from ingestion.custom_status_mappings import _normalize
from reporting.export import compute_control_coverage


def _raw(statuses):
    return pd.DataFrame({
        "username": [f"u{i}" for i in range(len(statuses))],
        "system": "BSS",
        "account_status": statuses,
        "last_login_date": "2026-09-20",
        "password_last_set": "2026-08-01",
        "role": "user",
    })


def test_normalize_keeps_sign():
    assert _normalize("-1") == "-1"
    assert _normalize("1") == "1"
    assert _normalize("+1") == "1"
    assert _normalize("01") == "1"
    assert _normalize("-1") != _normalize("1")


def test_normalize_text_unchanged():
    assert _normalize("Y-Active") == "y active"
    assert _normalize("  Not   Active ") == "not active"
    assert _normalize("Désactivé") == "desactive"


def test_tokens_keep_sign():
    assert _tokenize_status_value("-1") == {"-1"}
    assert _tokenize_status_value("1") == {"1"}
    assert _tokenize_status_value("Y-Active") == {"y", "active"}
    assert _tokenize_status_value("N-inactive") == {"n", "inactive"}


def test_minus_one_is_not_auto_active():
    assert _is_active_account("1") is True
    assert _is_active_account("-1") is False
    assert _is_active_account("Y-Active") is True
    assert _is_active_account("N-inactive") is False
    assert _is_active_account("Not Active") is False


def test_minus_one_unmapped_is_unknown_worst_case():
    df = analyze_access(_raw(["1", "-1"]))
    assert df["is_active_for_audit"].tolist() == [True, True]
    assert df["status_is_unknown"].tolist() == [False, True]


def test_mapping_minus_one_to_inactive_does_not_touch_ones():
    df = analyze_access(_raw(["1", "1", "-1", "0"]),
                        custom_status_mappings={_normalize("-1"): "inactive"})
    assert df["is_active_for_audit"].tolist() == [True, True, False, False]
    assert len(compute_control_coverage(df, {})) == 19


def test_mapping_minus_one_to_inactive_numeric_dtype():
    df = analyze_access(_raw([1, 1, -1, 0]),
                        custom_status_mappings={_normalize(-1): "inactive"})
    assert df["is_active_for_audit"].tolist() == [True, True, False, False]


def test_all_inactive_after_mapping_does_not_crash():
    df = analyze_access(_raw(["-1", "-1", "-1"]),
                        custom_status_mappings={_normalize("-1"): "inactive"})
    assert not df["is_active_for_audit"].any()
    assert len(compute_control_coverage(df, {})) == 19
