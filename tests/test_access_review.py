"""
Tests du module d'analyse de revue des accès.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd
from analysis.access_review import analyze_access, summarize


def test_terminated_but_active_flagged_critical():
    """Un employé parti avec un compte encore actif doit être détecté en priorité critique."""
    df = pd.DataFrame({
        "username": ["jdupont"],
        "system": ["Active Directory"],
        "account_status": ["Active"],
        "employee_status": ["Terminated"],
        "last_login_date": ["2026-08-01"],
    })
    result = analyze_access(df)
    assert result.loc[0, "is_terminated_but_active"] == True
    assert result.loc[0, "review_action"] == "Révoquer immédiatement"
    assert result.loc[0, "risk_level"] == "Critique"
    print("OK - test_terminated_but_active_flagged_critical")


def test_dormant_account_detected():
    """Un compte sans connexion depuis > seuil doit être flagué dormant."""
    df = pd.DataFrame({
        "username": ["old_user"],
        "system": ["VPN"],
        "last_login_date": ["2025-01-01"],  # largement > 90 jours avant aujourd'hui
    })
    result = analyze_access(df)
    assert result.loc[0, "is_dormant"] == True
    print("OK - test_dormant_account_detected")


def test_recent_login_not_dormant():
    """Un compte connecté récemment ne doit pas être flagué dormant."""
    df = pd.DataFrame({
        "username": ["active_user"],
        "system": ["SAP"],
        "last_login_date": [pd.Timestamp.now().strftime("%Y-%m-%d")],
    })
    result = analyze_access(df)
    assert result.loc[0, "is_dormant"] == False
    assert result.loc[0, "review_action"] == "Aucune action"
    print("OK - test_recent_login_not_dormant")


def test_privileged_dormant_is_critical():
    """Un compte privilégié dormant doit être considéré plus grave qu'un compte standard dormant."""
    df = pd.DataFrame({
        "username": ["admin_dormant", "standard_dormant"],
        "system": ["Active Directory", "Active Directory"],
        "is_privileged": ["Oui", "Non"],
        "last_login_date": ["2025-01-01", "2025-01-01"],
    })
    result = analyze_access(df)
    assert result.loc[0, "risk_level"] == "Critique"
    assert result.loc[1, "risk_level"] in ("Moyen", "Élevé")
    print("OK - test_privileged_dormant_is_critical")


def test_missing_optional_columns_no_crash():
    """Sans les colonnes optionnelles, l'analyse ne doit jamais planter."""
    df = pd.DataFrame({
        "username": ["minimal_user"],
        "system": ["CRM"],
    })
    result = analyze_access(df)
    assert "review_action" in result.columns
    assert "risk_level" in result.columns
    print("OK - test_missing_optional_columns_no_crash")


def test_summarize_counts_correctly():
    df = pd.DataFrame({
        "username": ["u1", "u2", "u3"],
        "system": ["AD", "AD", "AD"],
        "account_status": ["Active", "Active", "Active"],
        "employee_status": ["Terminated", "Active", "Active"],
        "is_privileged": ["Non", "Oui", "Non"],
        "last_login_date": ["2026-08-19", "2025-01-01", "2026-08-19"],
    })
    result = analyze_access(df)
    summary = summarize(result)
    assert summary["total_accounts"] == 3
    assert summary["terminated_but_active"] == 1
    assert summary["privileged_dormant"] == 1
    print(f"OK - test_summarize_counts_correctly ({summary})")


def test_ldap_generalized_time_parsed_correctly():
    """
    Le format de date LDAP/AD ('20260807120000.0Z') doit être reconnu,
    même si ce n'est pas un format ISO standard.
    """
    from analysis.access_review import _days_since
    from datetime import datetime, timedelta

    old_date = datetime.now() - timedelta(days=200)
    ldap_formatted = old_date.strftime("%Y%m%d%H%M%S") + ".0Z"

    days = _days_since(ldap_formatted)
    assert days is not None
    assert 199 <= days <= 201  # tolérance d'un jour pour l'exécution du test
    print(f"OK - test_ldap_generalized_time_parsed_correctly ({days} jours détectés)")


if __name__ == "__main__":
    test_terminated_but_active_flagged_critical()
    test_dormant_account_detected()
    test_recent_login_not_dormant()
    test_privileged_dormant_is_critical()
    test_missing_optional_columns_no_crash()
    test_summarize_counts_correctly()
    test_ldap_generalized_time_parsed_correctly()
    print("\nTous les tests sont passés.")
