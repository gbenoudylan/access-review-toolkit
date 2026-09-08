"""
Tests de la lecture universelle des fichiers .txt et .docx sans tableau.

Valide les 3 stratégies de lecture en cascade : délimité, colonnes
alignées par espaces, blocs clé-valeur — ainsi que le rejet propre d'un
texte réellement non structuré.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from ingestion.ingest import load_file, IngestionError


def test_txt_delimited(tmp_path):
    content = (
        "Username|System|Account Status|Employee Status\n"
        "jkonan|Active Directory|Active|Active\n"
        "bafolabi|SAP|Active|Terminated\n"
    )
    path = tmp_path / "delimited.txt"
    path.write_text(content, encoding="utf-8")

    df = load_file(path)
    assert len(df) == 2
    assert "username" in df.columns
    assert "system" in df.columns
    print("OK - test_txt_delimited")


def test_txt_fixed_width(tmp_path):
    content = (
        "Username    System              Account Status    Employee Status\n"
        "mkeita      Active Directory    Active            Active\n"
        "pyao        VPN                 Active            Terminated\n"
    )
    path = tmp_path / "fixedwidth.txt"
    path.write_text(content, encoding="utf-8")

    df = load_file(path)
    assert len(df) == 2
    assert "username" in df.columns
    print("OK - test_txt_fixed_width")


def test_txt_key_value_blocks(tmp_path):
    content = (
        "Nom d'utilisateur: sagbato\n"
        "Systeme: SIEM ArcSight\n"
        "Statut compte: Actif\n"
        "Statut RH: Actif\n"
        "\n"
        "Nom d'utilisateur: rzongo\n"
        "Systeme: CRM\n"
        "Statut compte: Actif\n"
        "Statut RH: Parti\n"
    )
    path = tmp_path / "keyvalue.txt"
    path.write_text(content, encoding="utf-8")

    df = load_file(path)
    assert len(df) == 2
    assert "username" in df.columns
    assert set(df["username"]) == {"sagbato", "rzongo"}
    print("OK - test_txt_key_value_blocks")


def test_txt_unreadable_raises_clear_error(tmp_path):
    content = (
        "Ceci est un simple paragraphe de texte libre, sans structure "
        "reconnaissable, comme le contenu d'un email ou d'une note."
    )
    path = tmp_path / "unreadable.txt"
    path.write_text(content, encoding="utf-8")

    try:
        load_file(path)
        assert False, "Une IngestionError aurait dû être levée"
    except IngestionError as e:
        assert "Impossible d'interpréter" in str(e)
        print("OK - test_txt_unreadable_raises_clear_error")


def test_docx_freetext_keyvalue(tmp_path):
    """.docx sans aucun tableau, données en paragraphes clé-valeur."""
    from docx import Document

    doc = Document()
    doc.add_heading("Fiches comptes", level=1)
    for username, system, status in [
        ("dyao", "Active Directory", "Parti"),
        ("kboni", "SAP", "Actif"),
    ]:
        doc.add_paragraph(f"Nom d'utilisateur: {username}")
        doc.add_paragraph(f"Systeme: {system}")
        doc.add_paragraph(f"Statut RH: {status}")
        doc.add_paragraph("")

    path = tmp_path / "freetext.docx"
    doc.save(str(path))

    df = load_file(path)
    assert len(df) == 2
    assert "username" in df.columns
    print("OK - test_docx_freetext_keyvalue")


if __name__ == "__main__":
    import tempfile
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        test_txt_delimited(tmp_path)
        test_txt_fixed_width(tmp_path)
        test_txt_key_value_blocks(tmp_path)
        test_txt_unreadable_raises_clear_error(tmp_path)
        test_docx_freetext_keyvalue(tmp_path)
    print("\nTous les tests sont passés.")


def test_missing_system_column_falls_back_to_filename():
    """
    Un export brut d'un seul système (ex. extraction AD pure) sans colonne
    'system' ne doit plus faire échouer l'ingestion : le nom du fichier
    sert de repli automatique.
    """
    import tempfile
    from ingestion.ingest import load_file

    content = "SAM Account Name,Display Name,Account Status\nadmin_test,Compte Test,Active\n"
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".csv", delete=False, prefix="ActiveDirectory_export_"
    ) as tmp:
        tmp.write(content)
        tmp_path = tmp.name

    df = load_file(tmp_path)
    assert "system" in df.columns
    assert df.loc[0, "system"] == Path(tmp_path).stem
    print(f"OK - test_missing_system_column_falls_back_to_filename (system={df.loc[0, 'system']!r})")


def test_missing_system_column_uses_explicit_default():
    """Un nom de système fourni explicitement prime sur le nom de fichier."""
    import tempfile
    from ingestion.ingest import load_file

    content = "SAM Account Name,Display Name,Account Status\nadmin_test,Compte Test,Active\n"
    with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as tmp:
        tmp.write(content)
        tmp_path = tmp.name

    df = load_file(tmp_path, default_system="Active Directory")
    assert df.loc[0, "system"] == "Active Directory"
    print("OK - test_missing_system_column_uses_explicit_default")


def test_duplicate_mapped_columns_are_merged_not_duplicated():
    """
    Deux colonnes sources distinctes qui pointent vers le même champ
    standard (ex. 'SAM Account Name' et 'Logon Name' -> toutes deux
    'username' dans un export AD) ne doivent jamais produire deux colonnes
    de même nom après standardisation — ça fait planter l'affichage en
    aval (Streamlit/Arrow). Elles doivent être fusionnées.
    """
    import pandas as pd
    from ingestion.ingest import standardize_columns

    df = pd.DataFrame({
        "SAM Account Name": ["jdupont", "kbrou"],
        "Logon Name": ["jdupont@corp.com", None],
        "Display Name": ["Jean Dupont", "Konan Brou"],
    })
    result = standardize_columns(df)
    assert len(result.columns) == len(set(result.columns)), "Colonnes dupliquées détectées"
    assert list(result["username"]) == ["jdupont", "kbrou"]
    print("OK - test_duplicate_mapped_columns_are_merged_not_duplicated")


def test_username_and_user_id_stay_separate():
    """
    'username' (nom de connexion) et 'user_id' (identifiant employé/
    matricule) sont deux informations distinctes : elles ne doivent pas
    être fusionnées en un seul champ, contrairement aux vraies variantes
    d'un même champ (ex. SAM Account Name / Logon Name).
    """
    import pandas as pd
    from ingestion.ingest import standardize_columns

    df = pd.DataFrame({
        "SAM Account Name": ["jdupont"],
        "Employee ID": ["EMP-4821"],
        "Display Name": ["Jean Dupont"],
    })
    result = standardize_columns(df)
    assert "username" in result.columns
    assert "user_id" in result.columns
    assert result.loc[0, "username"] == "jdupont"
    assert result.loc[0, "user_id"] == "EMP-4821"
    print("OK - test_username_and_user_id_stay_separate")


def test_server_export_headers_recognized():
    """
    En-têtes d'un export serveur/Linux (User, Sudo Privileges, Assigned
    User Roles, Last Password Reset Date) — un contexte différent des
    exports Active Directory déjà couverts, avec un vocabulaire propre.
    """
    from ingestion.ingest import _match_column

    assert _match_column("User") == "username"
    assert _match_column("Sudo Privileges") == "is_privileged"
    assert _match_column("Assigned User Roles") == "role"
    assert _match_column("Last Password Reset Date") == "password_last_set"
    print("OK - test_server_export_headers_recognized")


def test_user_variant_does_not_break_user_id_mapping():
    """
    Ajouter 'user' comme variante de username ne doit pas faire dévier
    'user_id'/'User ID'/'Employee ID' vers 'username' par accident — la
    correspondance exacte sur son propre champ doit toujours l'emporter.
    """
    from ingestion.ingest import _match_column

    assert _match_column("user_id") == "user_id"
    assert _match_column("User ID") == "user_id"
    assert _match_column("Employee ID") == "user_id"
    print("OK - test_user_variant_does_not_break_user_id_mapping")


def test_camelcase_ad_azure_headers_recognized():
    """En-têtes sans espace d'un export AD/Azure hybride (UserPrincipalName,
    LastLogonDate, PasswordLastSet, AccountExpirationDate) et 'When Changed'."""
    from ingestion.ingest import _match_column
    assert _match_column("UserPrincipalName") == "username"
    assert _match_column("LastLogonDate") == "last_login_date"
    assert _match_column("PasswordLastSet") == "password_last_set"
    assert _match_column("AccountExpirationDate") == "account_expiry_date"
    assert _match_column("When Changed") == "last_login_date"
    print("OK - test_camelcase_ad_azure_headers_recognized")


def test_first_last_name_synthesized_into_full_name():
    """
    Un export avec Prénom/Nom séparés (pas de colonne Display Name unique,
    ex. export O365) doit reconstituer 'full_name' automatiquement.
    """
    import tempfile
    from ingestion.ingest import load_file

    content = "First Name,Last Name,SamAccountName,AccountStatus\nJean,Dupont,jdupont,Active\n"
    with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as tmp:
        tmp.write(content)
        tmp_path = tmp.name

    df = load_file(tmp_path, default_system="O365")
    assert df.loc[0, "full_name"] == "Jean Dupont"
    print("OK - test_first_last_name_synthesized_into_full_name")


def test_full_name_synthesis_does_not_override_existing_display_name():
    """Si 'full_name' existe déjà (ex. Display Name), il ne doit pas être
    écrasé par la reconstitution First/Last Name."""
    import tempfile
    from ingestion.ingest import load_file

    content = "First Name,Last Name,Display Name,SamAccountName\nJean,Dupont,J. Dupont (IT),jdupont\n"
    with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as tmp:
        tmp.write(content)
        tmp_path = tmp.name

    df = load_file(tmp_path, default_system="Test")
    assert df.loc[0, "full_name"] == "J. Dupont (IT)"
    print("OK - test_full_name_synthesis_does_not_override_existing_display_name")


def test_informational_fields_mapped():
    """Champs informatifs (source_recommended_action, source_reason,
    owner_comment, phone) reconnus mais sans impact sur l'analyse."""
    from ingestion.ingest import _match_column
    assert _match_column("RecommendedAction") == "source_recommended_action"
    assert _match_column("Reason") == "source_reason"
    assert _match_column("Owner comment") == "owner_comment"
    assert _match_column("Mobile") == "phone"
    print("OK - test_informational_fields_mapped")


def test_txt_multiple_blocks_column_split_merged():
    """
    Avant correction, un fichier .txt avec deux blocs délimités séparés
    par une ligne vide (ex. 'identités' puis 'rôles' pour les mêmes
    comptes) traitait l'en-tête du second bloc comme une donnée, avec des
    valeurs qui glissaient dans les mauvaises colonnes. Doit maintenant
    fusionner proprement par colonne.
    """
    import tempfile
    from ingestion.ingest import load_file

    content = (
        "SAM Account Name,Display Name\n"
        "user1,Jean Dupont\n"
        "user2,Konan Brou\n"
        "\n"
        "SAM Account Name,Assigned User Roles\n"
        "user1,Admin\n"
        "user2,User\n"
    )
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as tmp:
        tmp.write(content)
        tmp_path = tmp.name

    df = load_file(tmp_path, default_system="Test")
    assert len(df) == 2
    row = df[df["username"] == "user1"].iloc[0]
    assert row["full_name"] == "Jean Dupont"
    assert row["role"] == "Admin"
    print("OK - test_txt_multiple_blocks_column_split_merged")


def test_txt_multiple_blocks_different_accounts_stacked():
    """Deux blocs .txt avec des comptes différents doivent rester empilés."""
    import tempfile
    from ingestion.ingest import load_file

    content = (
        "SAM Account Name,Account Status\n"
        "user1,Active\n"
        "user2,Active\n"
        "\n"
        "SAM Account Name,Account Status\n"
        "user3,Active\n"
    )
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as tmp:
        tmp.write(content)
        tmp_path = tmp.name

    df = load_file(tmp_path, default_system="Test")
    assert len(df) == 3
    print("OK - test_txt_multiple_blocks_different_accounts_stacked")


def test_exact_duplicate_raw_column_names_not_lost():
    """
    Deux colonnes brutes portant EXACTEMENT le même libellé (pas juste
    équivalent, ex. deux colonnes "Status") faisaient perdre silencieuse-
    ment toutes les données correspondantes — df["Status"] renvoie les
    deux colonnes à la fois (un DataFrame, pas une Series) quand les
    labels sont identiques, ce qui cassait la fusion. Les valeurs doivent
    maintenant être préservées (la première des deux fait foi).
    """
    import tempfile
    from ingestion.ingest import load_file

    content = "SAM Account Name,Status,Status\nuser1,Active,Enabled\n"
    with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as tmp:
        tmp.write(content)
        tmp_path = tmp.name

    df = load_file(tmp_path, default_system="Test")
    assert "account_status" in df.columns
    assert df.loc[0, "account_status"] == "Active"
    print("OK - test_exact_duplicate_raw_column_names_not_lost")


def test_entirely_empty_required_field_raises_clear_error():
    """
    Une colonne obligatoire présente mais 100% vide (mapping probablement
    tombé sur la mauvaise colonne source) doit lever une erreur claire,
    pas produire silencieusement un rapport rempli de comptes anonymes.
    """
    from ingestion.ingest import validate_required_fields, IngestionError
    import pandas as pd

    df = pd.DataFrame({"username": [None, None], "system": ["AD", "AD"]})
    try:
        validate_required_fields(df)
        assert False, "Aucune erreur levée alors que 'username' est entièrement vide"
    except IngestionError as e:
        assert "entièrement vide" in str(e)
    print("OK - test_entirely_empty_required_field_raises_clear_error")


def test_partially_empty_required_field_does_not_raise():
    """
    Seulement QUELQUES lignes vides (pas toutes) ne doit pas déclencher
    l'erreur de champ vide — seul un champ à 100% vide est concerné.
    """
    from ingestion.ingest import validate_required_fields
    import pandas as pd

    df = pd.DataFrame({"username": ["user1", None], "system": ["AD", "AD"]})
    validate_required_fields(df)  # ne doit pas lever d'exception
    print("OK - test_partially_empty_required_field_does_not_raise")


def test_bare_created_column_recognized():
    """'CREATED' seul (sans 'date'), rencontré sur un export réel, doit
    être reconnu comme account_created_date."""
    from ingestion.ingest import _match_column
    assert _match_column("CREATED") == "account_created_date"
    print("OK - test_bare_created_column_recognized")


def test_data_quality_report_detects_real_issues():
    """
    Contrôle qualité des données AVANT analyse : doit détecter usernames
    manquants, doublons, dates invalides (cohérent avec la vraie logique
    de parsing, y compris les dates tronquées déjà corrigées), statuts
    non reconnus, et calculer une fiabilité globale sensée.
    """
    from ingestion.ingest import compute_data_quality_report
    import pandas as pd

    df = pd.DataFrame({
        "username": ["u1", None, "u1", "u4", "u5"],
        "system": ["AD"] * 5,
        "account_status": ["Active", "Active", "GarbageValue123", "Active", "Active"],
        "last_login_date": ["2026-01-01", "4 20:09:01 +0000 2025", "Never", "2026-01-01", None],
        "manager": ["Jean", "", "Paul", None, "Marie"],
    })
    report = compute_data_quality_report(df)
    assert report["total_rows"] == 5
    assert report["issues"]["username_missing"] == 1
    assert report["issues"]["duplicate_usernames"] == 2
    assert report["issues"]["invalid_dates"] == 1
    assert report["issues"]["unknown_status"] == 1
    assert report["reliability_pct"] == 40.0
    print("OK - test_data_quality_report_detects_real_issues")


def test_data_quality_report_clean_file_full_reliability():
    """Un fichier sans aucun problème doit afficher 100% de fiabilité."""
    from ingestion.ingest import compute_data_quality_report
    import pandas as pd

    df = pd.DataFrame({
        "username": ["u1", "u2"], "system": ["AD"] * 2,
        "account_status": ["Active", "Disabled"],
        "last_login_date": ["2026-01-01", "2025-06-01"],
    })
    report = compute_data_quality_report(df)
    assert report["reliability_pct"] == 100.0
    print("OK - test_data_quality_report_clean_file_full_reliability")
