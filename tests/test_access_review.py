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


def test_never_logged_in_account_flagged_as_dormant():
    """
    Un compte sans aucune date de dernière connexion ('Never Logon Status',
    catégorie d'exception documentée à part entière dans un vrai rapport
    d'audit) doit être détecté — via is_never_used, contrôle distinct du
    dormant classique depuis la séparation Dormant/Never Used — pas exclu
    de toute détection faute de date à comparer.
    """
    df = pd.DataFrame({
        "username": ["never_logged_in"],
        "system": ["Active Directory"],
        "last_login_date": [None],
    })
    result = analyze_access(df)
    assert result.loc[0, "is_never_used"] == True
    print("OK - test_never_logged_in_account_flagged_as_dormant")


def test_never_recommends_deletion():
    """
    Politique retenue : jamais de recommandation de suppression, uniquement
    de désactivation (réversible, sans besoin d'historique). Verrouille ce
    choix pour qu'il ne soit jamais réintroduit par erreur plus tard.
    """
    df = pd.DataFrame({
        "username": ["u1", "u2", "u3"],
        "system": ["Active Directory", "Active Directory", "Active Directory"],
        "last_login_date": [None, "2020-01-01", "2026-09-01"],
        "is_privileged": ["Admin", "Non", "Non"],
    })
    result = analyze_access(df)
    assert not result["review_action"].str.contains("upprim", case=False).any()
    print("OK - test_never_recommends_deletion")


def test_excel_serial_date_converted_correctly():
    """
    Un numéro de série Excel (ex. 45678, quand une colonne de date perd
    son formatage) doit être interprété comme une vraie date Excel
    (jours depuis le 30/12/1899), pas comme des nanosecondes depuis 1970
    (interprétation par défaut de pandas sur un entier brut, qui
    produisait silencieusement une date fausse de plusieurs dizaines
    d'années).
    """
    from analysis.access_review import _days_since
    from datetime import datetime, timedelta

    days = _days_since(45678)
    assert days is not None
    computed_date = datetime.now() - timedelta(days=days)
    expected_date = datetime(1899, 12, 30) + timedelta(days=45678)
    assert computed_date.date() == expected_date.date()
    print("OK - test_excel_serial_date_converted_correctly")


def test_implausible_bare_number_not_treated_as_date():
    """Un nombre hors plage plausible (ex. 5, 999999) ne doit pas être
    interprété comme une date Excel — trop de risque de faux positif."""
    from analysis.access_review import _days_since
    assert _days_since("5") is None
    assert _days_since("999999") is None
    print("OK - test_implausible_bare_number_not_treated_as_date")


def test_ambiguous_date_interpreted_day_first():
    """
    '03/04/2026' doit être lu comme le 3 avril (jour/mois/année, standard
    francophone/africain), pas le 4 mars (mois/jour/année, standard
    américain que pandas utilise par défaut) — contexte MTN oblige.
    """
    from analysis.access_review import _days_since
    from datetime import datetime, timedelta
    days = _days_since("03/04/2026")
    assert days is not None
    computed_date = datetime.now() - timedelta(days=days)
    assert computed_date.month == 4 and computed_date.day == 3
    print("OK - test_ambiguous_date_interpreted_day_first")


def test_iso_date_still_correct_with_dayfirst():
    """Le format ISO (non ambigu) doit rester correct malgré dayfirst=True."""
    from analysis.access_review import _days_since
    from datetime import datetime, timedelta
    days = _days_since("2026-01-15")
    computed_date = datetime.now() - timedelta(days=days)
    assert computed_date.month == 1 and computed_date.day == 15
    print("OK - test_iso_date_still_correct_with_dayfirst")


def test_never_text_markers_treated_as_never_logged_in():
    """
    Des valeurs texte comme 'Never', 'N/A', 'Jamais' dans la colonne de
    dernière connexion (terminologie vue dans un vrai rapport d'audit,
    'Never Logon Status') doivent être traitées comme un compte jamais
    connecté (is_never_used) — pas silencieusement ignorées.
    """
    df = pd.DataFrame({
        "username": ["u1", "u2", "u3"], "system": ["AD"] * 3,
        "last_login_date": ["Never", "N/A", "Jamais"],
    })
    result = analyze_access(df)
    assert result["is_never_used"].all()
    print("OK - test_never_text_markers_treated_as_never_logged_in")


def test_iso_date_with_ambiguous_day_month_not_flipped():
    """
    Régression réelle : dayfirst=True (ajouté pour lever l'ambiguïté
    JJ/MM/AAAA) inversait à tort une date ISO déjà non ambiguë quand jour
    ET mois valaient tous les deux <= 12 (ex. '2026-09-01' devenait le
    9 janvier au lieu du 1er septembre, une inversion jour/mois).
    """
    from analysis.access_review import _days_since
    from datetime import datetime, timedelta

    days = _days_since("2026-09-01")
    assert days is not None
    computed = datetime.now() - timedelta(days=days)
    assert (computed.month, computed.day) == (9, 1), (
        f"Attendu le 1er septembre, obtenu {computed.month}/{computed.day} "
        f"— la date ISO a été inversée jour/mois"
    )
    print("OK - test_iso_date_with_ambiguous_day_month_not_flipped")


def test_numeric_boolean_status_recognized():
    """'1' comme statut de compte (export brut LDAP/base de données où les
    booléens sont stockés en 1/0) doit être reconnu comme actif, cohérent
    avec 'true'/'yes'/'oui' déjà traités ainsi."""
    from analysis.access_review import _is_active_account
    assert _is_active_account("1") == True
    assert _is_active_account("0") == False
    print("OK - test_numeric_boolean_status_recognized")


def test_dayfirst_detected_from_column_evidence_monthfirst():
    """
    Une colonne contenant au moins une date avec le second groupe > 12
    (ex. '03/25/2026') prouve sans ambiguïté un format mois-premier
    (MM/JJ, américain) — la colonne entière doit alors être lue ainsi,
    y compris les dates par ailleurs ambiguës du même lot.
    """
    from analysis.access_review import _detect_dayfirst
    import pandas as pd
    series = pd.Series(["11/12/2025", "03/25/2026"])
    assert _detect_dayfirst(series) == (False, "proven")


def test_dayfirst_detected_from_column_evidence_dayfirst():
    """Un premier groupe > 12 (ex. '25/03/2026') prouve sans ambiguïté un
    format jour-premier (JJ/MM)."""
    from analysis.access_review import _detect_dayfirst
    import pandas as pd
    series = pd.Series(["03/04/2026", "25/03/2026"])
    assert _detect_dayfirst(series) == (True, "proven")


def test_dayfirst_defaults_true_without_evidence():
    """Sans aucune preuve dans la colonne (tous les groupes <= 12), le
    repli par défaut reste jour-premier (standard MTN)."""
    from analysis.access_review import _detect_dayfirst
    import pandas as pd
    series = pd.Series(["03/04/2026", "01/02/2026"])
    assert _detect_dayfirst(series) == (True, "guessed")


def test_real_world_us_format_with_time_and_timezone():
    """
    Cas réel rencontré : '11/12/2025 10:27:25.000000000 AM +00' provenant
    d'un système source utilisant le format américain (MM/JJ/AAAA), avec
    heure, nanosecondes et fuseau horaire. Doit être lu comme le 12
    novembre 2025 (mois-premier) quand une autre valeur de la même
    colonne le confirme, pas le 11 décembre (jour-premier, faux ici).
    """
    df = pd.DataFrame({
        "username": ["u1", "u2"], "system": ["AD"] * 2,
        "last_login_date": ["11/12/2025 10:27:25.000000000 AM +00", "03/25/2026"],
    })
    result = analyze_access(df)
    from datetime import datetime, timedelta
    days = result.loc[0, "days_since_last_login"]
    computed = datetime.now() - timedelta(days=int(days))
    assert computed.month == 11, f"Attendu novembre (mois-premier), obtenu mois={computed.month}"
    print("OK - test_real_world_us_format_with_time_and_timezone")


def test_yearfirst_detected_from_unambiguous_evidence():
    """Un 1er groupe > 31 (ex. '97-03-10') ne peut être qu'une année ->
    confirme la convention année-en-premier pour toute la colonne."""
    from analysis.access_review import _detect_yearfirst
    import pandas as pd
    series = pd.Series(["26-01-15", "97-03-10"])
    assert _detect_yearfirst(series) == (True, "proven")


def test_yearfirst_false_without_evidence():
    """Sans preuve (tous les 1ers groupes <= 31), yearfirst ne doit pas
    être forcé à tort."""
    from analysis.access_review import _detect_yearfirst
    import pandas as pd
    series = pd.Series(["15-01-26", "10-03-26"])
    assert _detect_yearfirst(series) == (False, "guessed")


def test_two_digit_year_first_resolved_via_column_evidence():
    """
    Cas réel : une colonne mêlant des dates AA-MM-JJ ambiguës isolément
    (ex. '26-01-15') avec au moins une valeur qui prouve sans ambiguïté
    la convention (ex. '97-03-10', où 97 ne peut être qu'une année) doit
    correctement dater TOUTES les valeurs de la colonne selon cette
    convention, y compris celles qui restent ambiguës individuellement.
    """
    df = pd.DataFrame({
        "username": ["u1", "u2"], "system": ["AD"] * 2,
        "last_login_date": ["26-01-15", "97-03-10"],
    })
    result = analyze_access(df)
    from datetime import datetime, timedelta
    computed = datetime.now() - timedelta(days=int(result.loc[0, "days_since_last_login"]))
    assert (computed.year, computed.month, computed.day) == (2026, 1, 15)
    print("OK - test_two_digit_year_first_resolved_via_column_evidence")


def test_oracle_open_status_recognized_as_active():
    """'OPEN' (statut Oracle DB standard pour un compte utilisable) doit
    être reconnu comme actif — sinon un employé parti avec un compte
    Oracle 'OPEN' échappe entièrement à la détection critique."""
    from analysis.access_review import _is_active_account
    assert _is_active_account("open") == True
    assert _is_active_account("OPEN") == True
    print("OK - test_oracle_open_status_recognized_as_active")


def test_locked_account_not_counted_as_dormant():
    """
    Un compte verrouillé ne doit pas être compté comme dormant — le
    contrôle standard scope la dormance aux comptes 'in active status'.
    Un compte verrouillé est déjà bloqué, catégorie distincte (is_locked).
    """
    df = pd.DataFrame({
        "username": ["u1", "u2"], "system": ["Oracle"] * 2,
        "account_status": ["open", "locked"],
        "last_login_date": ["2025-01-01"] * 2,
    })
    result = analyze_access(df)
    assert result.loc[0, "is_dormant"] == True   # open + vieux login -> dormant
    assert result.loc[1, "is_dormant"] == False  # locked -> pas dormant
    assert result.loc[1, "is_locked"] == True
    print("OK - test_locked_account_not_counted_as_dormant")


def test_additional_hr_terminated_status_values_recognized():
    """
    'fired', 'retired', 'dismissed', 'licencié' (variantes RH réalistes
    non couvertes avant) doivent déclencher la détection critique d'un
    compte actif d'employé parti, comme 'terminated'/'resigned' déjà.
    """
    for status in ["fired", "retired", "dismissed", "licencié"]:
        df = pd.DataFrame({
            "username": ["u1"], "system": ["AD"],
            "account_status": ["Active"], "employee_status": [status],
        })
        result = analyze_access(df)
        assert result.loc[0, "is_terminated_but_active"] == True, f"Échec pour '{status}'"
    print("OK - test_additional_hr_terminated_status_values_recognized")


def test_truncated_date_not_guessed_wrong():
    """
    Un format tronqué réel ('4 20:09:01 +0000 2025', sans jour de semaine
    ni mois) faisait deviner à pandas un MOIS à partir du nombre isolé,
    avec un jour arbitraire (1) inventé — ex. '4 ...' lu comme le 1er
    avril, une date totalement fausse et silencieuse. Doit maintenant
    être reconnu comme non exploitable plutôt que deviné.
    """
    from analysis.access_review import _days_since
    assert _days_since("4 20:09:01 +0000 2025") is None
    assert _days_since("7 13:04:09 +0000 2019") is None
    print("OK - test_truncated_date_not_guessed_wrong")


def test_ctime_style_dates_parsed_correctly():
    """Les formats ctime réels (avec et sans jour de semaine) doivent
    rester correctement exploitables — seul le format TRONQUÉ doit être
    rejeté, pas le format complet."""
    from analysis.access_review import _days_since
    assert _days_since("Sat Dec 14 17:30:34 +0000 2019") is not None
    assert _days_since("May 12 01:55:01 +0000 2022") is not None
    print("OK - test_ctime_style_dates_parsed_correctly")


def test_no_data_marker_treated_as_never_logged_in():
    """'No Data' (marqueur réel rencontré) doit être traité comme
    'jamais connecté' (is_never_used) — pas silencieusement ignoré."""
    df = pd.DataFrame({
        "username": ["u1"], "system": ["AD"], "last_login_date": ["No Data"],
    })
    result = analyze_access(df)
    assert result.loc[0, "is_never_used"] == True
    print("OK - test_no_data_marker_treated_as_never_logged_in")


def test_administrator_role_alone_flags_privileged():
    """
    Un compte avec seulement role='Administrator' (sans colonne booléenne
    'Sudo Privileges' séparée — cas réel d'export serveur) doit être
    détecté comme privilégié, pas seulement via une colonne dédiée.
    """
    df = pd.DataFrame({
        "username": ["u1", "u2", "u3"], "system": ["Server"] * 3,
        "role": ["User", "Administrator", "root"],
    })
    result = analyze_access(df)
    assert result.loc[0, "is_privileged_flag"] == False
    assert result.loc[1, "is_privileged_flag"] == True
    assert result.loc[2, "is_privileged_flag"] == True
    print("OK - test_administrator_role_alone_flags_privileged")


def test_admin_role_detection_uses_word_boundaries_not_substring():
    """
    Régression réelle et sévère : la détection par simple sous-chaîne
    ('admin' in role) faisait remonter en masse des faux positifs sur
    des intitulés qui contiennent 'admin' sans être un privilège IT réel
    ('Administrative Assistant', 'Sales Administration'...) — repéré sur
    un vrai fichier où ça faisait passer ~60% des comptes comme
    'privilégié', un chiffre irréaliste qui aurait complètement faussé
    l'audit. Doit détecter par mot entier, pas par simple inclusion.
    """
    df = pd.DataFrame({
        "username": [f"u{i}" for i in range(4)], "system": ["Server"] * 4,
        "role": ["Administrative Assistant", "Administration", "Sales Administration", "System Administrator"],
    })
    result = analyze_access(df)
    assert result.loc[0, "is_privileged_flag"] == False, "Administrative Assistant ne doit PAS être privilégié"
    assert result.loc[1, "is_privileged_flag"] == False, "Administration (service) ne doit PAS être privilégié"
    assert result.loc[2, "is_privileged_flag"] == False, "Sales Administration ne doit PAS être privilégié"
    assert result.loc[3, "is_privileged_flag"] == True, "System Administrator DOIT rester privilégié"
    print("OK - test_admin_role_detection_uses_word_boundaries_not_substring")


def test_password_threshold_is_90_not_180():
    """Le seuil doit être 90 jours (standard interne MTN confirmé),
    pas 180 — un ancien réglage qui n'avait jamais été propagé ici."""
    from analysis.access_review import PASSWORD_STALE_THRESHOLD_DAYS
    assert PASSWORD_STALE_THRESHOLD_DAYS == 90


def test_service_account_excluded_from_password_rotation_policy():
    """
    Le contrôle 14 exclut explicitement les comptes de service de la
    règle de rotation à 90 jours. Un mot de passe périmé sur un compte
    de service ne doit ni déclencher 'Exiger un changement de mot de
    passe', ni faire monter le niveau de risque comme un compte humain.
    """
    df = pd.DataFrame({
        "username": ["jdupont", "svc_backup"], "system": ["AD"] * 2,
        "password_last_set": ["2024-01-01"] * 2,
        "last_login_date": ["2026-09-01"] * 2,
    })
    result = analyze_access(df)
    human = result[result["username"] == "jdupont"].iloc[0]
    service = result[result["username"] == "svc_backup"].iloc[0]
    assert human["review_action"] == "Exiger un changement de mot de passe"
    assert human["risk_level"] == "Moyen"
    assert "compte de service" in service["review_action"]
    assert service["risk_level"] == "Faible"
    print("OK - test_service_account_excluded_from_password_rotation_policy")


def test_dormant_and_never_used_are_properly_separated():
    """
    Vraie séparation des contrôles 2 et 6 : 'Dormant' suppose une
    connexion déjà survenue (juste ancienne) ; 'Never Used' est un compte
    jamais connecté ET créé depuis plus de 30 jours. Un compte jamais
    connecté mais créé très récemment (< 30 jours) ne doit être ni
    dormant ni 'never used' — trop tôt pour le signaler.
    """
    import datetime as dt
    recent_creation = (dt.datetime.now() - dt.timedelta(days=5)).strftime("%Y-%m-%d")
    old_creation = (dt.datetime.now() - dt.timedelta(days=200)).strftime("%Y-%m-%d")

    df = pd.DataFrame({
        "username": ["stale_login", "never_used_old", "never_used_recent"],
        "system": ["AD"] * 3,
        "account_created_date": [old_creation, old_creation, recent_creation],
        "last_login_date": ["2024-01-01", None, None],
    })
    result = analyze_access(df)
    stale = result[result["username"] == "stale_login"].iloc[0]
    never_old = result[result["username"] == "never_used_old"].iloc[0]
    never_recent = result[result["username"] == "never_used_recent"].iloc[0]

    assert stale["is_dormant"] == True and stale["is_never_used"] == False
    assert never_old["is_never_used"] == True and never_old["is_dormant"] == False
    assert never_recent["is_never_used"] == False, "Créé il y a 5 jours seulement : trop tôt pour signaler"
    print("OK - test_dormant_and_never_used_are_properly_separated")


def test_test_account_naming_pattern_detected_without_false_positives():
    """
    Contrôle 4 : détection par convention de nommage (test_user, uat_,
    qa_, dummy_, sandbox_...). Le mot-clé 'test' est volontairement
    détecté SANS frontière de mot stricte (compromis assumé après
    preuve réelle sur un export client : 'testadmin', 'dtest',
    'sdptester', 'MTNtester' — le mot-clé est presque toujours accolé
    directement à un autre fragment, jamais isolé par un séparateur ;
    une frontière stricte comme pour les autres mots-clés ratait la
    quasi-totalité de ces comptes réels). Un nom de famille contenant
    incidemment 'test' ('Testard') peut donc désormais être signalé à
    tort — accepté comme le bon compromis pour un contrôle de sécurité :
    un faux positif se rejette en un coup d'œil, un vrai compte de test
    jamais détecté ne se rattrape pas. Les autres mots-clés ('uat',
    'qa', 'dummy', 'demo', 'sandbox'), plus courts ou plus fréquents
    comme fragment de mot ordinaire, gardent leur frontière stricte
    faute de preuve équivalente qu'ils s'accolent aussi en pratique.
    """
    df = pd.DataFrame({
        "username": ["test_user", "uat_admin", "jtestard", "contest_manager", "jdupont",
                      "testadmin", "dtest", "sdptester", "MTNtester", "qatar_ops"],
        "system": ["AD"] * 10,
    })
    result = analyze_access(df)
    assert result.loc[0, "is_test_account"] == True
    assert result.loc[1, "is_test_account"] == True
    assert result.loc[2, "is_test_account"] == True, "Compromis assumé : 'test' sans frontière"
    assert result.loc[3, "is_test_account"] == True, "Compromis assumé : 'test' sans frontière"
    assert result.loc[4, "is_test_account"] == False
    assert result.loc[5, "is_test_account"] == True, "Cas réel rencontré (export client)"
    assert result.loc[6, "is_test_account"] == True, "Cas réel rencontré (export client)"
    assert result.loc[7, "is_test_account"] == True, "Cas réel rencontré (export client)"
    assert result.loc[8, "is_test_account"] == True, "Cas réel rencontré (export client)"
    assert result.loc[9, "is_test_account"] == False, "'qa' garde sa frontière stricte (Qatar)"
    print("OK - test_test_account_naming_pattern_detected_without_false_positives")


def test_naming_convention_tolerates_accents_and_numeric_suffix():
    """
    Contrôle 9 : la règle 'initiale prénom + nom' doit tolérer les
    accents (noms africains/français) et un suffixe numérique de
    doublon légitime (jdupont, jdupont2...), sans faux positif.
    """
    df = pd.DataFrame({
        "username": ["mbrown", "jdupont", "kbrou2"],
        "full_name": ["Michael Brown", "Jean Dupont", "Konan Brou"],
        "system": ["AD"] * 3,
    })
    result = analyze_access(df)
    assert not result["is_non_compliant_naming"].any()
    print("OK - test_naming_convention_tolerates_accents_and_numeric_suffix")


def test_naming_convention_flags_real_mismatch():
    """Un username sans rapport avec le nom réel doit être signalé."""
    df = pd.DataFrame({
        "username": ["random123"], "full_name": ["Marie Curie"], "system": ["AD"],
    })
    result = analyze_access(df)
    assert result.loc[0, "is_non_compliant_naming"] == True
    print("OK - test_naming_convention_flags_real_mismatch")


def test_naming_convention_not_checked_without_full_name():
    """Sans nom complet disponible, le contrôle ne doit rien inventer."""
    df = pd.DataFrame({"username": ["jdupont"], "system": ["AD"]})
    result = analyze_access(df)
    assert result.loc[0, "is_non_compliant_naming"] == False
    print("OK - test_naming_convention_not_checked_without_full_name")


def test_risk_score_capped_at_100_and_explainable():
    """Le score doit être plafonné à 100 même en cumulant tous les
    signaux, et chaque composante doit être traçable (raison + points)."""
    df = pd.DataFrame({
        "username": ["jdupont"], "system": ["AD"],
        "account_status": ["Active"], "employee_status": ["Terminated"],
        "is_privileged": ["Yes"], "password_last_set": ["2024-01-01"],
        "last_login_date": ["2025-01-01"],
    })
    result = analyze_access(df)
    score = result.loc[0, "risk_score"]
    reasons = result.loc[0, "risk_score_reasons"]
    assert score == 100
    assert sum(pts for _, pts in reasons) >= 100  # le brut dépasse 100, plafonné à l'affichage
    assert ("Employé parti, compte encore actif", 50) in reasons
    print("OK - test_risk_score_capped_at_100_and_explainable")


def test_risk_score_zero_for_clean_account():
    """Un compte sans aucun signal doit avoir un score de 0."""
    df = pd.DataFrame({
        "username": ["clean_user"], "system": ["AD"],
        "account_status": ["Active"], "last_login_date": ["2026-09-01"],
    })
    result = analyze_access(df)
    assert result.loc[0, "risk_score"] == 0
    assert result.loc[0, "risk_score_reasons"] == []
    print("OK - test_risk_score_zero_for_clean_account")


def test_dormant_threshold_is_configurable():
    """Le seuil de dormance doit être un vrai paramètre, pas figé —
    recommandé dans plusieurs retours externes, jamais fait avant."""
    df = pd.DataFrame({"username": ["u1"], "system": ["AD"], "last_login_date": ["2026-08-01"]})
    r90 = analyze_access(df, dormant_threshold_days=90)
    r30 = analyze_access(df, dormant_threshold_days=30)
    assert r90.loc[0, "is_dormant"] != r30.loc[0, "is_dormant"]
    print("OK - test_dormant_threshold_is_configurable")


def test_password_stale_threshold_is_configurable():
    """Le seuil d'ancienneté du mot de passe doit être configurable, pas
    seulement une constante de module fixe."""
    df = pd.DataFrame({"username": ["u1"], "system": ["AD"], "password_last_set": ["2026-08-01"]})
    r90 = analyze_access(df, password_stale_threshold_days=90)
    r30 = analyze_access(df, password_stale_threshold_days=30)
    assert r90.loc[0, "is_password_stale"] != r30.loc[0, "is_password_stale"]
    print("OK - test_password_stale_threshold_is_configurable")


def test_never_used_threshold_is_configurable():
    """Le délai de grâce avant de signaler un compte 'jamais utilisé'
    doit être configurable."""
    import datetime as dt
    creation = (dt.datetime.now() - dt.timedelta(days=20)).strftime("%Y-%m-%d")
    df = pd.DataFrame({
        "username": ["u1"], "system": ["AD"],
        "account_created_date": [creation], "last_login_date": [None],
    })
    r30 = analyze_access(df, never_used_threshold_days=30)
    r10 = analyze_access(df, never_used_threshold_days=10)
    assert r30.loc[0, "is_never_used"] == False, "Créé il y a 20j, seuil 30j : trop tôt"
    assert r10.loc[0, "is_never_used"] == True, "Créé il y a 20j, seuil 10j : doit signaler"
    print("OK - test_never_used_threshold_is_configurable")


def test_load_custom_sod_matrix_from_excel():
    """La matrice SoD doit pouvoir être chargée depuis un fichier Excel à
    2 colonnes, peu importe leur nom exact — recommandé pour que chaque
    entreprise adapte les conflits sans modifier le code."""
    from analysis.sod_detection import load_custom_sod_matrix, detect_sod_conflicts
    import io

    buf = io.BytesIO()
    pd.DataFrame({
        "role_1": ["Créateur Paiement", "Développeur"],
        "role_2": ["Approbateur Paiement", "Admin Prod"],
    }).to_excel(buf, index=False)
    matrix = load_custom_sod_matrix(buf.getvalue(), "matrice.xlsx")
    assert matrix == [("Créateur Paiement", "Approbateur Paiement"), ("Développeur", "Admin Prod")]

    df = pd.DataFrame({
        "username": ["u1"], "system": ["ERP"],
        "role": ["Créateur Paiement, Approbateur Paiement"],
    })
    result = detect_sod_conflicts(df, conflicts=matrix)
    assert result.loc[0, "sod_conflict"] == True
    print("OK - test_load_custom_sod_matrix_from_excel")


def test_load_custom_sod_matrix_from_csv():
    """Doit aussi fonctionner avec un CSV, pas seulement Excel."""
    from analysis.sod_detection import load_custom_sod_matrix
    content = "role_1,role_2\nAdmin,Auditeur\n"
    matrix = load_custom_sod_matrix(content.encode(), "matrice.csv")
    assert matrix == [("Admin", "Auditeur")]
    print("OK - test_load_custom_sod_matrix_from_csv")


def test_french_month_names_recognized():
    """
    Vrai bug trouvé : les mois en français complets ('Avril', 'Juin',
    'Mars') n'étaient pas du tout reconnus par le parseur (anglais par
    défaut) et retournaient silencieusement None — alors que certaines
    abréviations françaises ('Sept.', 'Oct.') passaient par pure
    coïncidence avec l'anglais, masquant le problème.
    """
    from analysis.access_review import _days_since
    from datetime import datetime, timedelta
    tests = [("Avril 27, 2022", 4, 27, 2022), ("Juin 14, 2022", 6, 14, 2022), ("Mars 15, 2023", 3, 15, 2023)]
    for value, exp_month, exp_day, exp_year in tests:
        days = _days_since(value)
        assert days is not None, f"'{value}' aurait dû être reconnu"
        computed = datetime.now() - timedelta(days=int(days))
        assert (computed.year, computed.month, computed.day) == (exp_year, exp_month, exp_day), value
    print("OK - test_french_month_names_recognized")


def test_date_without_year_resolved_to_recent_occurrence():
    """
    Vrai bug trouvé : une date sans année (ex. 'Fri Jan 17 16:05', motif
    d'horodatage type journal système) était interprétée par pandas comme
    l'année 1 (0001) — une date absurde, sans la moindre erreur visible.
    Doit être ramenée à l'occurrence récente la plus plausible.
    """
    from analysis.access_review import _days_since
    days = _days_since("Fri Jan 17 16:05")
    assert days is not None
    assert days < 3650, "Ne doit pas rester sur l'année 1 (des milliers d'années d'écart)"
    print("OK - test_date_without_year_resolved_to_recent_occurrence")


def test_no_info_marker_treated_as_never_logged_in():
    """'No info' (nouveau marqueur réel rencontré) doit être traité comme
    'jamais connecté' (is_never_used), comme 'Never'/'No Data' déjà."""
    df = pd.DataFrame({"username": ["u1"], "system": ["AD"], "last_login_date": ["No info"]})
    result = analyze_access(df)
    assert result.loc[0, "is_never_used"] == True
    print("OK - test_no_info_marker_treated_as_never_logged_in")


def test_reference_datetime_makes_analysis_reproducible():
    """
    Une revue doit pouvoir être rejouée à l'identique des mois plus tard
    (reproductibilité d'audit) — deux appels avec la MÊME date de
    référence doivent produire EXACTEMENT le même résultat, peu importe
    quand ils sont réellement exécutés.
    """
    from datetime import datetime
    df = pd.DataFrame({"username": ["u1"], "system": ["AD"], "last_login_date": ["2026-01-01"]})
    fixed_ref = datetime(2026, 9, 9, 12, 0, 0)
    r1 = analyze_access(df, reference_datetime=fixed_ref)
    r2 = analyze_access(df, reference_datetime=fixed_ref)
    assert r1["days_since_last_login"].equals(r2["days_since_last_login"])
    print("OK - test_reference_datetime_makes_analysis_reproducible")


def test_future_login_date_flagged_not_silently_ignored():
    """Une date de dernière connexion dans le futur est une anomalie de
    donnée à part entière, signalée séparément (last_login_future)."""
    df = pd.DataFrame({"username": ["u1"], "system": ["AD"], "last_login_date": ["2099-01-01"]})
    result = analyze_access(df)
    assert result.loc[0, "last_login_future"] == True
    assert result.loc[0, "is_dormant"] == False  # ne doit pas non plus être faussement dormant
    print("OK - test_future_login_date_flagged_not_silently_ignored")


def test_temporal_inconsistency_creation_after_login_detected():
    """
    Un compte ne peut pas s'être connecté AVANT sa propre date de
    création — anomalie métier détectée même si chaque date est
    syntaxiquement valide individuellement.
    """
    df = pd.DataFrame({
        "username": ["u1"], "system": ["AD"],
        "account_created_date": ["2024-01-01"], "last_login_date": ["2023-01-01"],
    })
    result = analyze_access(df)
    assert result.loc[0, "temporal_inconsistency"] == True
    print("OK - test_temporal_inconsistency_creation_after_login_detected")


def test_temporal_consistency_normal_case_not_flagged():
    """Le cas normal (connexion après création) ne doit jamais être
    signalé à tort comme incohérent."""
    df = pd.DataFrame({
        "username": ["u1"], "system": ["AD"],
        "account_created_date": ["2024-01-01"], "last_login_date": ["2024-06-01"],
    })
    result = analyze_access(df)
    assert result.loc[0, "temporal_inconsistency"] == False
    print("OK - test_temporal_consistency_normal_case_not_flagged")


def test_date_convention_confidence_distinguishes_proven_from_guessed():
    """
    Fiabilité maximale sur les dates : l'outil doit distinguer une
    convention JJ/MM réellement PROUVÉE par une valeur de la colonne
    d'un simple repli par défaut faute de preuve — visible dans le
    résultat, pas juste devinée en silence avec la même assurance.
    """
    guessed = pd.DataFrame({"username": ["u1"], "system": ["AD"], "last_login_date": ["03/04/2026"]})
    r_guessed = analyze_access(guessed)
    assert r_guessed.loc[0, "last_login_date_convention_uncertain"] == True
    assert summarize(r_guessed)["date_convention_uncertain"] == True

    proven = pd.DataFrame({
        "username": ["u1", "u2"], "system": ["AD"] * 2,
        "last_login_date": ["03/04/2026", "25/03/2026"],
    })
    r_proven = analyze_access(proven)
    assert r_proven.loc[0, "last_login_date_convention_uncertain"] == False
    assert summarize(r_proven)["date_convention_uncertain"] == False
    print("OK - test_date_convention_confidence_distinguishes_proven_from_guessed")


def test_mixed_convention_column_resolves_each_row_independently():
    """
    Demande explicite : les formats peuvent changer en cours de colonne
    (export fusionnant plusieurs systèmes sources, chacun avec sa propre
    convention JJ/MM ou MM/JJ). Chaque ligne ayant SA PROPRE preuve
    individuelle doit être déterminée correctement, indépendamment de la
    convention majoritaire du reste de la colonne — et une ligne
    individuellement ambiguë dans une colonne prouvée mélangée doit
    rester honnêtement indéterminable plutôt que devinée au hasard via
    la convention majoritaire (qui pourrait très bien ne pas être la
    sienne).
    """
    df = pd.DataFrame({
        "username": ["u1", "u2", "u3"], "system": ["AD"] * 3,
        # u1 : ne peut être que jour-premier (25 > 12 en 1re position)
        # u2 : ne peut être que mois-premier (25 > 12 en 2e position)
        # u3 : ambigu, aucune preuve propre
        "last_login_date": ["25/03/2026", "03/25/2026", "03/04/2026"],
    })
    result = analyze_access(df)
    assert result.loc[0, "last_login_date_convention_status"] == "mixed"
    # u1 et u2 pointent en réalité vers la même vraie date calendaire
    # (25 mars 2026) une fois chacun lu selon SA PROPRE convention prouvée.
    assert result.loc[0, "days_since_last_login"] == result.loc[1, "days_since_last_login"]
    # u3, individuellement ambigu dans une colonne mélangée, doit rester
    # non résolu plutôt que rattaché à une convention qui pourrait être
    # la mauvaise pour cette ligne précise.
    assert pd.isna(result.loc[2, "days_since_last_login"])
    print("OK - test_mixed_convention_column_resolves_each_row_independently")


def test_yearfirst_can_prove_year_last_position():
    """
    Vrai bug trouvé (même principe qu'appliqué à _detect_dayfirst) :
    l'ancienne version ne pouvait JAMAIS prouver que l'année est en
    DERNIÈRE position (seulement le deviner par défaut, avec la même
    apparence qu'une vraie preuve) — alors qu'un 3e groupe > 31 le prouve
    tout aussi directement qu'un 1er groupe > 31 prouve l'inverse.
    """
    from analysis.access_review import _detect_yearfirst
    result = _detect_yearfirst(pd.Series(["15-01-45"]))
    assert result == (False, "proven")
    print("OK - test_yearfirst_can_prove_year_last_position")


def test_yearfirst_mixed_column_detected():
    """Une colonne mélangeant réellement les deux positions d'année doit
    être détectée comme telle, pas silencieusement résolue par la
    première preuve rencontrée."""
    from analysis.access_review import _detect_yearfirst
    result = _detect_yearfirst(pd.Series(["45-01-15", "15-01-45"]))
    assert result[1] == "mixed"
    print("OK - test_yearfirst_mixed_column_detected")


def test_comma_separated_date_refused_not_guessed():
    """
    Vrai bug trouvé par balayage systématique : pandas ne respecte PAS
    dayfirst/yearfirst pour un format séparé par des virgules, et peut
    même perdre un groupe entier sans la moindre erreur ('01,25,2026' lu
    comme 1er janvier 2026, le 25 disparaissant purement et simplement).
    Refusé explicitement plutôt que risqué, y compris quand un groupe
    serait individuellement non ambigu (25 > 12).
    """
    from analysis.access_review import _days_since
    for value in ["12,01,2026", "25,01,2026", "01,25,2026", "2026,01,12"]:
        assert _days_since(value) is None, f"{value!r} aurait dû être refusé"
    print("OK - test_comma_separated_date_refused_not_guessed")


def test_no_warning_leak_on_excess_precision_timestamp():
    """
    Vrai bug trouvé : un timestamp avec une précision sub-microseconde
    excessive (9 décimales) faisait fuiter un UserWarning pandas non
    supprimé (le filtre ne couvrait que le pd.to_datetime() lui-même, pas
    la conversion .to_pydatetime() qui suit, où le warning se produit
    réellement) — pollue les journaux en usage réel sans rapport avec un
    vrai risque d'erreur pour un calcul d'ancienneté en jours.
    """
    import warnings
    from analysis.access_review import _days_since
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        result = _days_since("2026-09-09 12:00:00.123456789")
    assert result is not None
    print("OK - test_no_warning_leak_on_excess_precision_timestamp")


def test_single_letter_y_recognized_as_active():
    """
    Vrai gap trouvé : 'Y' (Oui/Non en une lettre, convention très
    courante dans les exports issus de bases de données SQL) n'était pas
    reconnu comme actif, alors que 'yes' l'était déjà. 'A', volontairement
    ambigu (Active ? Approved ? Available ?), reste à raison non reconnu —
    ce n'est pas un oubli mais un choix délibéré de ne pas deviner.
    """
    from analysis.access_review import _is_active_account
    assert _is_active_account("Y") == True
    assert _is_active_account("y") == True
    assert _is_active_account("A") == False  # ambigu, volontairement non reconnu
    print("OK - test_single_letter_y_recognized_as_active")


def test_single_letter_y_recognized_for_privileged_and_password_status():
    """
    Même gap que pour le statut actif, trouvé par cohérence : 'Y'
    n'était pas reconnu pour is_privileged non plus — corrigé de la même
    façon. Ajout aussi de synonymes clairs et non ambigus pour le mot de
    passe permanent ('No expiry', 'Does not expire'), sans ajouter de
    termes trop vagues comme 'Permanent' seul.
    """
    from analysis.access_review import _is_privileged, _has_non_expiring_password
    assert _is_privileged("Y") == True
    assert _has_non_expiring_password("No expiry") == True
    assert _has_non_expiring_password("Does not expire") == True
    print("OK - test_single_letter_y_recognized_for_privileged_and_password_status")


def test_duplicate_detection_normalizes_case_and_whitespace():
    """
    Vrai bug trouvé, sérieux : le contrôle 8 (Duplicate Accounts) ratait
    complètement 'Jean Dupont' vs 'JEAN DUPONT' (casse différente selon
    le système source) et 'Jean Dupont' vs ' Jean Dupont ' (espaces
    parasites, très fréquents en pratique) — la comparaison se faisait
    sur le nom brut, sans normalisation.
    """
    case_df = pd.DataFrame({
        "username": ["jdupont", "jdupont2"], "system": ["AD", "AD"],
        "full_name": ["Jean Dupont", "JEAN DUPONT"], "account_status": ["Active", "Active"],
    })
    result_case = analyze_access(case_df)
    assert result_case["is_duplicate_account"].tolist() == [True, True]

    whitespace_df = pd.DataFrame({
        "username": ["jdupont", "jdupont2"], "system": ["AD", "AD"],
        "full_name": ["Jean Dupont", " Jean Dupont "], "account_status": ["Active", "Active"],
    })
    result_ws = analyze_access(whitespace_df)
    assert result_ws["is_duplicate_account"].tolist() == [True, True]

    # Pas de faux positif pour des personnes réellement différentes
    different_df = pd.DataFrame({
        "username": ["jdupont", "mmartin"], "system": ["AD", "AD"],
        "full_name": ["Jean Dupont", "Marie Martin"], "account_status": ["Active", "Active"],
    })
    result_diff = analyze_access(different_df)
    assert result_diff["is_duplicate_account"].tolist() == [False, False]
    print("OK - test_duplicate_detection_normalizes_case_and_whitespace")


def test_unknown_password_change_treated_as_stale_not_ignored():
    """
    Demande explicite de fiabilité maximale, cohérente avec le principe
    déjà appliqué à 'last_login_date' (compte jamais connecté = signalé,
    pas ignoré) : une date de dernier changement de mot de passe qu'on
    ne peut PAS déterminer ('No info', vide, ou simplement un format non
    interprétable) doit être traitée comme le pire cas plutôt que
    silencieusement considérée comme "pas de souci". Pour un audit de
    sécurité, l'absence d'info est au moins aussi préoccupante qu'une
    rotation ancienne mais connue.
    """
    df = pd.DataFrame({
        "username": ["u1", "u2", "u3", "u4"], "system": ["AD"] * 4,
        "password_last_set": ["2020-01-01", "No info", "", None],
    })
    result = analyze_access(df)
    assert result["is_password_stale"].tolist() == [True, True, True, True]
    assert result["password_change_unknown"].tolist() == [False, True, True, True]
    print("OK - test_unknown_password_change_treated_as_stale_not_ignored")


def test_password_column_entirely_absent_control_stays_disabled():
    """Si la colonne 'password_last_set' est ENTIÈREMENT absente du
    fichier, le contrôle reste désactivé (pas de faux positif en masse)
    — distinct du cas d'une valeur manquante ligne par ligne au sein
    d'une colonne réellement présente."""
    df = pd.DataFrame({"username": ["u1"], "system": ["AD"]})
    result = analyze_access(df)
    assert result.loc[0, "is_password_stale"] == False
    print("OK - test_password_column_entirely_absent_control_stays_disabled")


def test_locked_detection_not_fooled_by_negation():
    """
    Vrai bug trouvé : 'Unlocked' (l'inverse exact !), 'Déverrouillé' et
    'Débloqué' contiennent respectivement 'locked'/'verrouillé'/'bloqué'
    comme sous-chaîne et étaient signalés à tort comme verrouillés — la
    détection se faisait sur une simple sous-chaîne, sans frontière de
    mot. Corrigé avec \\b, qui exclut correctement ces négations tout en
    gardant la détection des vraies formes verrouillées.
    """
    df = pd.DataFrame({
        "username": ["u1", "u2", "u3", "u4", "u5"], "system": ["AD"] * 5,
        "account_status": ["Unlocked", "Déverrouillé", "Débloqué", "Locked", "Verrouillé"],
    })
    result = analyze_access(df)
    assert result["is_locked"].tolist() == [False, False, False, True, True]
    print("OK - test_locked_detection_not_fooled_by_negation")


def test_unparseable_but_present_login_date_treated_as_worst_case():
    """
    Vrai bug trouvé en aidant un utilisateur : un format de date de
    dernière connexion tronqué (jour de semaine et mois manquants, ex.
    '4 20:09:01 +0000 2025', rencontré en pratique sur un export réel)
    laissait le compte silencieusement hors du contrôle de dormance
    (days_since_last_login = None, ni compté ni signalé). Corrigé avec
    le même principe que pour le mot de passe non renseigné : une
    connexion a bien eu lieu (donc ce n'est pas 'jamais connecté'), mais
    sa date précise reste inconnue — traité comme pire cas plutôt
    qu'ignoré, avec un drapeau distinct pour rester honnête sur ce qu'on
    sait vraiment.
    """
    df = pd.DataFrame({
        "username": ["u1", "u2", "u3"], "system": ["AD"] * 3,
        "last_login_date": ["2026-09-01", "4 20:09:01 +0000 2025", "Never Logged In"],
        "account_status": ["Active"] * 3,
    })
    result = analyze_access(df, reference_datetime=pd.Timestamp("2026-09-12"))
    # u1 : date récente et exploitable -> pas dormant, pas de faux positif
    assert result.loc[0, "is_dormant"] == False
    assert result.loc[0, "last_login_date_unparseable"] == False
    # u2 : format tronqué -> signalé comme pire cas, drapeau distinct
    assert result.loc[1, "is_dormant"] == True
    assert result.loc[1, "last_login_date_unparseable"] == True
    assert result.loc[1, "review_action"] == "Vérifier (date de dernière connexion non exploitable)"
    # u3 : jamais connecté (marqueur explicite) -> is_never_used, PAS ce nouveau drapeau
    assert result.loc[2, "last_login_date_unparseable"] == False
    print("OK - test_unparseable_but_present_login_date_treated_as_worst_case")
