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


def test_password_stale_detected():
    """Un mot de passe non changé depuis > seuil doit être signalé périmé."""
    df = pd.DataFrame({
        "username": ["old_pwd_user"],
        "system": ["Active Directory"],
        "password_last_set": ["2025-01-01"],  # largement > 180 jours avant aujourd'hui
    })
    result = analyze_access(df)
    assert result.loc[0, "is_password_stale"] == True
    assert result.loc[0, "review_action"] == "Exiger un changement de mot de passe"
    print("OK - test_password_stale_detected")


def test_recent_password_not_stale():
    """Un mot de passe changé récemment ne doit pas être signalé périmé."""
    df = pd.DataFrame({
        "username": ["fresh_pwd_user"],
        "system": ["Active Directory"],
        "password_last_set": [pd.Timestamp.now().strftime("%Y-%m-%d")],
    })
    result = analyze_access(df)
    assert result.loc[0, "is_password_stale"] == False
    print("OK - test_recent_password_not_stale")


def test_privileged_non_expiring_password_is_critical():
    """
    Un compte privilégié dont le mot de passe n'expire jamais est un risque
    critique, même sans autre anomalie (dormance, statut RH...).
    """
    df = pd.DataFrame({
        "username": ["admin_svc"],
        "system": ["Active Directory"],
        "is_privileged": ["Admin"],
        "password_status": ["Never Expires"],
        "last_login_date": [pd.Timestamp.now().strftime("%Y-%m-%d")],  # connexion récente, pas dormant
    })
    result = analyze_access(df)
    assert result.loc[0, "has_non_expiring_password"] == True
    assert result.loc[0, "risk_level"] == "Critique"
    assert result.loc[0, "review_action"] == "Forcer l'expiration du mot de passe (privilégié)"
    print("OK - test_privileged_non_expiring_password_is_critical")


def test_column_mapping_recognizes_ad_export_headers():
    """
    Les en-têtes standard d'un export Active Directory (avec espaces, tels
    qu'ils apparaissent réellement à l'export) doivent être reconnus, y
    compris ceux dont le score de similarité avec la variante existante
    passait sous le seuil de correspondance (ex. 'SAM Account Name' vs
    'samaccountname' : 73% < 85% de seuil) avant l'ajout des variantes
    espacées explicites.
    """
    from ingestion.ingest import _match_column

    assert _match_column("SAM Account Name") == "username"
    assert _match_column("Display Name") == "full_name"
    assert _match_column("Logon Name") == "username"
    assert _match_column("When Created") == "account_created_date"
    assert _match_column("Password Last Set") == "password_last_set"
    assert _match_column("Password Expiry Date") == "password_expiry_date"
    assert _match_column("Account Expiry Time") == "account_expiry_date"
    assert _match_column("Password Status") == "password_status"
    print("OK - test_column_mapping_recognizes_ad_export_headers")


if __name__ == "__main__":
    test_terminated_but_active_flagged_critical()
    test_dormant_account_detected()
    test_recent_login_not_dormant()
    test_privileged_dormant_is_critical()
    test_missing_optional_columns_no_crash()
    test_summarize_counts_correctly()
    test_ldap_generalized_time_parsed_correctly()
    test_password_stale_detected()
    test_recent_password_not_stale()
    test_privileged_non_expiring_password_is_critical()
    test_column_mapping_recognizes_ad_export_headers()
    print("\nTous les tests sont passés.")


def test_service_account_naming_convention_detected():
    """Convention svc_* / *_svc reconnue, générique au secteur."""
    from analysis.access_review import _is_service_account_name
    assert _is_service_account_name("svc_backup") == True
    assert _is_service_account_name("backup_svc") == True
    assert _is_service_account_name("jdupont") == False
    print("OK - test_service_account_naming_convention_detected")


def test_dormant_service_account_gets_verification_action_not_disable():
    """
    Un compte de service dormant doit être vérifié auprès du propriétaire
    technique, pas désactivé directement comme un compte humain dormant.
    """
    df = pd.DataFrame({
        "username": ["svc_backup"],
        "system": ["Active Directory"],
        "last_login_date": ["2025-01-01"],  # ancien -> dormant
    })
    result = analyze_access(df)
    assert result.loc[0, "is_service_account"] == True
    assert result.loc[0, "review_action"] == "Vérifier avec le propriétaire technique (compte de service)"
    print("OK - test_dormant_service_account_gets_verification_action_not_disable")


def test_duplicate_active_accounts_detected():
    """Deux comptes actifs pour la même personne sur le même système = doublon."""
    df = pd.DataFrame({
        "username": ["jdupont1", "jdupont2", "kbrou"],
        "full_name": ["Jean Dupont", "Jean Dupont", "Konan Brou"],
        "system": ["CRM", "CRM", "CRM"],
        "account_status": ["Active", "Active", "Active"],
    })
    result = analyze_access(df)
    assert result.loc[0, "is_duplicate_account"] == True
    assert result.loc[1, "is_duplicate_account"] == True
    assert result.loc[2, "is_duplicate_account"] == False
    assert result.loc[0, "review_action"] == "Fusionner les doublons (ne garder qu'un compte actif)"
    print("OK - test_duplicate_active_accounts_detected")


if __name__ == "__main__":
    test_service_account_naming_convention_detected()
    test_dormant_service_account_gets_verification_action_not_disable()
    test_duplicate_active_accounts_detected()
