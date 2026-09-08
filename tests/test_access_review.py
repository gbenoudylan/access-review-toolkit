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
    d'audit) doit être détecté comme dormant, pas exclu du contrôle faute
    de date à comparer.
    """
    df = pd.DataFrame({
        "username": ["never_logged_in"],
        "system": ["Active Directory"],
        "last_login_date": [None],
    })
    result = analyze_access(df)
    assert result.loc[0, "is_dormant"] == True
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
    connecté — donc dormant — pas silencieusement ignorées.
    """
    df = pd.DataFrame({
        "username": ["u1", "u2", "u3"], "system": ["AD"] * 3,
        "last_login_date": ["Never", "N/A", "Jamais"],
    })
    result = analyze_access(df)
    assert result["is_dormant"].all()
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
    assert _detect_dayfirst(series) == False


def test_dayfirst_detected_from_column_evidence_dayfirst():
    """Un premier groupe > 12 (ex. '25/03/2026') prouve sans ambiguïté un
    format jour-premier (JJ/MM)."""
    from analysis.access_review import _detect_dayfirst
    import pandas as pd
    series = pd.Series(["03/04/2026", "25/03/2026"])
    assert _detect_dayfirst(series) == True


def test_dayfirst_defaults_true_without_evidence():
    """Sans aucune preuve dans la colonne (tous les groupes <= 12), le
    repli par défaut reste jour-premier (standard MTN)."""
    from analysis.access_review import _detect_dayfirst
    import pandas as pd
    series = pd.Series(["03/04/2026", "01/02/2026"])
    assert _detect_dayfirst(series) == True


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
    assert _detect_yearfirst(series) == True


def test_yearfirst_false_without_evidence():
    """Sans preuve (tous les 1ers groupes <= 31), yearfirst ne doit pas
    être forcé à tort."""
    from analysis.access_review import _detect_yearfirst
    import pandas as pd
    series = pd.Series(["15-01-26", "10-03-26"])
    assert _detect_yearfirst(series) == False


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
    'jamais connecté' — dormant — pas silencieusement ignoré."""
    df = pd.DataFrame({
        "username": ["u1"], "system": ["AD"], "last_login_date": ["No Data"],
    })
    result = analyze_access(df)
    assert result.loc[0, "is_dormant"] == True
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
