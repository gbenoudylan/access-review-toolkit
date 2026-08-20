"""
Tests des modules d'enrichissement : croisement IAM+RH, conflits SoD,
workflow de validation.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd
from analysis.hr_crossref import cross_reference_with_hr
from analysis.sod_detection import detect_sod_conflicts
from analysis.review_workflow import (
    apply_review_decision, attach_review_status, review_summary,
)


# --------------------------- Croisement RH ---------------------------

def test_hr_crossref_detects_terminated_employee():
    """
    Le cas central : un compte IAM sans statut RH natif (comme un LDIF pur)
    doit récupérer le vrai statut depuis le fichier RH.
    """
    iam_df = pd.DataFrame({
        "username": ["jdupont", "mfofana"],
        "system": ["Active Directory", "Active Directory"],
        "account_status": ["Active", "Active"],
        # pas de colonne employee_status : cas d'un export LDAP pur
    })
    hr_df = pd.DataFrame({
        "hr_username": ["jdupont", "mfofana"],
        "hr_employee_status": ["Active", "Terminated"],
    })

    result = cross_reference_with_hr(iam_df, hr_df=hr_df)
    assert result.loc[result["username"] == "mfofana", "employee_status"].iloc[0] == "Terminated"
    assert result.loc[result["username"] == "jdupont", "employee_status"].iloc[0] == "Active"
    print("OK - test_hr_crossref_detects_terminated_employee")


def test_hr_crossref_flags_unmatched_accounts():
    """Un compte IAM sans correspondance RH doit être marqué comme tel, pas ignoré silencieusement."""
    iam_df = pd.DataFrame({
        "username": ["ghost_account"],
        "system": ["SAP"],
    })
    hr_df = pd.DataFrame({
        "hr_username": ["someone_else"],
        "hr_employee_status": ["Active"],
    })

    result = cross_reference_with_hr(iam_df, hr_df=hr_df)
    assert "Inconnu" in result.loc[0, "employee_status"]
    print("OK - test_hr_crossref_flags_unmatched_accounts")


# --------------------------- Conflits SoD ---------------------------

def test_sod_conflict_detected():
    df = pd.DataFrame({
        "username": ["kbrou", "kbrou", "jdupont"],
        "role": ["Créer paiement", "Valider paiement", "Standard"],
    })
    result = detect_sod_conflicts(df)
    assert result[result["username"] == "kbrou"]["sod_conflict"].all()
    assert not result[result["username"] == "jdupont"]["sod_conflict"].any()
    print("OK - test_sod_conflict_detected")


def test_sod_no_conflict_for_single_role():
    df = pd.DataFrame({
        "username": ["jdupont"],
        "role": ["Créer paiement"],
    })
    result = detect_sod_conflicts(df)
    assert not result.loc[0, "sod_conflict"]
    print("OK - test_sod_no_conflict_for_single_role")


def test_sod_conflict_within_single_combined_role_field():
    """Un seul champ role listant plusieurs rôles séparés par une virgule doit aussi être détecté."""
    df = pd.DataFrame({
        "username": ["kbrou"],
        "role": ["Créer paiement, Valider paiement"],
    })
    result = detect_sod_conflicts(df)
    assert result.loc[0, "sod_conflict"]
    print("OK - test_sod_conflict_within_single_combined_role_field")


def test_sod_missing_role_column_no_crash():
    df = pd.DataFrame({"username": ["u1"], "system": ["AD"]})
    result = detect_sod_conflicts(df)
    assert "sod_conflict" in result.columns
    assert not result["sod_conflict"].any()
    print("OK - test_sod_missing_role_column_no_crash")


# --------------------------- Workflow de validation ---------------------------

def test_workflow_default_status_is_pending(tmp_path):
    store_path = tmp_path / "decisions.json"
    df = pd.DataFrame({"username": ["u1"], "system": ["AD"]})
    result = attach_review_status(df, store_path=store_path)
    assert result.loc[0, "review_status"] == "En attente"
    print("OK - test_workflow_default_status_is_pending")


def test_workflow_persists_decision(tmp_path):
    store_path = tmp_path / "decisions.json"
    apply_review_decision(
        "u1", "AD", "Révoqué", validated_by="Manager X",
        comment="Confirmé parti", store_path=store_path,
    )

    df = pd.DataFrame({"username": ["u1"], "system": ["AD"]})
    result = attach_review_status(df, store_path=store_path)

    assert result.loc[0, "review_status"] == "Révoqué"
    assert result.loc[0, "validated_by"] == "Manager X"
    print("OK - test_workflow_persists_decision")


def test_workflow_decision_specific_to_user_and_system():
    """Le même username sur deux systèmes différents doit avoir des statuts indépendants."""
    import tempfile
    with tempfile.TemporaryDirectory() as tmp_dir:
        store_path = Path(tmp_dir) / "decisions.json"
        apply_review_decision("u1", "AD", "Révoqué", store_path=store_path)

        df = pd.DataFrame({
            "username": ["u1", "u1"],
            "system": ["AD", "SAP"],
        })
        result = attach_review_status(df, store_path=store_path)

        assert result.loc[result["system"] == "AD", "review_status"].iloc[0] == "Révoqué"
        assert result.loc[result["system"] == "SAP", "review_status"].iloc[0] == "En attente"
    print("OK - test_workflow_decision_specific_to_user_and_system")


def test_workflow_summary_counts():
    import tempfile
    with tempfile.TemporaryDirectory() as tmp_dir:
        store_path = Path(tmp_dir) / "decisions.json"
        apply_review_decision("u1", "AD", "Révoqué", store_path=store_path)
        apply_review_decision("u2", "AD", "Validé - accès légitime", store_path=store_path)

        df = pd.DataFrame({
            "username": ["u1", "u2", "u3"],
            "system": ["AD", "AD", "AD"],
        })
        result = attach_review_status(df, store_path=store_path)
        summary = review_summary(result)

        assert summary["revoque"] == 1
        assert summary["valide"] == 1
        assert summary["en_attente"] == 1
    print(f"OK - test_workflow_summary_counts ({summary})")


if __name__ == "__main__":
    import tempfile

    test_hr_crossref_detects_terminated_employee()
    test_hr_crossref_flags_unmatched_accounts()
    test_sod_conflict_detected()
    test_sod_no_conflict_for_single_role()
    test_sod_conflict_within_single_combined_role_field()
    test_sod_missing_role_column_no_crash()

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        test_workflow_default_status_is_pending(tmp_path)
        test_workflow_persists_decision(tmp_path)
    test_workflow_decision_specific_to_user_and_system()
    test_workflow_summary_counts()

    print("\nTous les tests sont passés.")
