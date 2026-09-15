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


def test_sod_conflict_detected_across_systems_with_different_case():
    """
    Vrai bug trouvé : la même personne apparaissant avec une casse
    différente selon le système source (ex. 'jdupont' sur AD, 'JDupont'
    sur SAP — deux systèmes maintenus séparément) faisait scinder ses
    rôles en deux identités distinctes, ratant un conflit SoD réparti
    sur plusieurs systèmes (ex. créateur de paiement sur l'un,
    validateur sur l'autre) — exactement le genre de conflit que ce
    contrôle doit attraper.
    """
    import pandas as pd
    from analysis.sod_detection import detect_sod_conflicts

    df = pd.DataFrame({
        "username": ["jdupont", "JDupont"], "system": ["AD", "SAP"],
        "role": ["Créer Paiement", "Valider Paiement"],
    })
    result = detect_sod_conflicts(df)
    assert result["sod_conflict"].tolist() == [True, True]

    # Deux personnes réellement différentes ne doivent pas être fusionnées
    different_df = pd.DataFrame({
        "username": ["jdupont", "mmartin"], "system": ["AD", "SAP"],
        "role": ["Créer Paiement", "Valider Paiement"],
    })
    result_diff = detect_sod_conflicts(different_df)
    assert result_diff["sod_conflict"].tolist() == [False, False]
    print("OK - test_sod_conflict_detected_across_systems_with_different_case")


def test_sod_conflict_detected_across_systems_with_different_identifiers():
    """
    Trouvé en répondant à une question de fiabilité : un identifiant
    technique diffère très souvent d'un système à l'autre (pas juste
    une casse différente — 'jdupont' sur AD vs 'jean.dupont' sur SAP),
    ce que la normalisation de casse seule ne peut pas relier. Le nom
    complet reste souvent la seule donnée commune : utilisé en repli,
    avec un marquage explicite de confiance moindre (homonymes possibles)
    plutôt que mélangé aux conflits confirmés par identifiant.
    """
    import pandas as pd
    from analysis.sod_detection import detect_sod_conflicts

    df = pd.DataFrame({
        "username": ["jdupont", "jean.dupont"], "system": ["AD", "SAP"],
        "full_name": ["Jean Dupont", "Jean Dupont"],
        "role": ["Créer Paiement", "Valider Paiement"],
    })
    result = detect_sod_conflicts(df)
    assert result["sod_conflict"].tolist() == [True, True]
    assert "homonymes" in result.loc[0, "sod_conflict_detail"]
    print("OK - test_sod_conflict_detected_across_systems_with_different_identifiers")


def test_sod_name_fallback_does_not_override_username_based_confidence_label():
    """Quand le conflit est déjà confirmé par identifiant (même
    personne, casse différente), le détail ne doit pas être remplacé
    par le libellé de moindre confiance du repli par nom."""
    import pandas as pd
    from analysis.sod_detection import detect_sod_conflicts

    df = pd.DataFrame({
        "username": ["jdupont", "JDupont"], "system": ["AD", "SAP"],
        "full_name": ["Jean Dupont", "Jean Dupont"],
        "role": ["Créer Paiement", "Valider Paiement"],
    })
    result = detect_sod_conflicts(df)
    assert result["sod_conflict"].tolist() == [True, True]
    assert "homonymes" not in result.loc[0, "sod_conflict_detail"]
    print("OK - test_sod_name_fallback_does_not_override_username_based_confidence_label")


def test_sod_matrix_ignores_self_referential_pairs():
    """
    Vrai bug trouvé en poussant la fiabilité au maximum : une ligne de
    la matrice SoD où les deux rôles sont identiques (ex. 'Admin'/
    'Admin' — une erreur de saisie plausible dans un tableur maintenu à
    la main) faisait signaler à tort TOUT compte portant simplement ce
    rôle une seule fois comme étant en conflit — un rôle ne peut pas
    être en conflit avec lui-même.
    """
    from analysis.sod_detection import load_custom_sod_matrix, detect_sod_conflicts
    import pandas as pd

    content = b"role_1,role_2\nAdmin,Admin\nCreateur,Validateur\n"
    pairs = load_custom_sod_matrix(content, "matrix.csv")
    assert pairs == [("Createur", "Validateur")]

    df = pd.DataFrame({"username": ["u1"], "system": ["AD"], "role": ["Admin"]})
    result = detect_sod_conflicts(df, conflicts=pairs)
    assert result.loc[0, "sod_conflict"] == False
    print("OK - test_sod_matrix_ignores_self_referential_pairs")


def test_role_splitting_preserves_comma_in_role_name_when_semicolon_present():
    """
    Vrai bug trouvé en poussant la fiabilité au maximum : un nom de rôle
    métier peut légitimement contenir une virgule ('Manager, Finance
    Department') — convertir systématiquement ';' en ',' avant de
    découper les rôles multi-valués coupait ce rôle UNIQUE en deux
    fragments, pouvant déclencher un faux conflit SoD si ces fragments
    correspondaient par coïncidence à une paire de la matrice.

    Corrigé pour le cas où un point-virgule est disponible comme
    séparateur non ambigu entre plusieurs rôles (dont un contient une
    virgule) — le cas où la SEULE virgule présente fait partie du nom
    d'un rôle unique, sans aucun point-virgule pour lever l'ambiguïté,
    reste un cas non résolu : structurellement impossible à distinguer
    de deux rôles séparés sans information supplémentaire.
    """
    from analysis.sod_detection import _split_roles, detect_sod_conflicts
    import pandas as pd

    assert _split_roles("Manager, Finance Department; Payment Creator") == [
        "Manager, Finance Department", " Payment Creator",
    ]

    df = pd.DataFrame({
        "username": ["u1"], "system": ["AD"],
        "role": ["Manager, Finance Department; Payment Validator"],
    })
    result = detect_sod_conflicts(df, conflicts=[("Payment Creator", "Payment Validator")])
    assert result.loc[0, "sod_conflict"] == False
    print("OK - test_role_splitting_preserves_comma_in_role_name_when_semicolon_present")
