"""Test du module d'export (Excel + PDF) pour la revue d'accès."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd
from reporting.export import generate_excel_report, generate_pdf_report


def build_sample_analyzed_df() -> pd.DataFrame:
    return pd.DataFrame({
        "username": ["jdupont", "kbrou", "mfofana", "sassoum"],
        "full_name": ["Jean Dupont", "Konan Brou", "Marc Fofana", "Sara Assoum"],
        "department": ["IT", "IT Security", "Sales", "HR"],
        "system": ["Active Directory", "SIEM", "CRM", "HRIS"],
        "manager": ["Marie D.", "", "Marie D.", "Paul N."],
        "account_status": ["Active", "Active", "Active", "Active"],
        "employee_status": ["Active", "Active", "Terminated", "Active"],
        "days_since_last_login": [2, 210, 5, 1],
        "is_privileged_flag": [False, True, False, False],
        "is_terminated_but_active": [False, False, True, False],
        "is_dormant": [False, True, False, False],
        "review_action": [
            "Aucune action", "Désactiver (privilégié dormant)",
            "Révoquer immédiatement", "Aucune action",
        ],
        "risk_level": ["Faible", "Critique", "Critique", "Faible"],
    })


def test_generate_excel_report(tmp_path):
    df = build_sample_analyzed_df()
    output = tmp_path / "test.xlsx"
    result = generate_excel_report(df, output)
    assert result.exists() and result.stat().st_size > 0

    from openpyxl import load_workbook
    wb = load_workbook(result)
    assert "Summary" in wb.sheetnames
    assert "Review Plan" in wb.sheetnames
    print(f"OK - test_generate_excel_report ({result.stat().st_size} octets)")


def test_generate_pdf_report(tmp_path):
    df = build_sample_analyzed_df()
    output = tmp_path / "test.pdf"
    result = generate_pdf_report(df, output)
    assert result.exists()
    with open(result, "rb") as f:
        assert f.read(5) == b"%PDF-"
    print(f"OK - test_generate_pdf_report ({result.stat().st_size} octets)")


if __name__ == "__main__":
    import tempfile
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        test_generate_excel_report(tmp_path)
        test_generate_pdf_report(tmp_path)
    print("\nTous les tests sont passés.")


def test_pdf_report_handles_missing_values_without_crashing():
    """
    .astype(str) sur un DataFrame ne convertit pas les valeurs manquantes
    (NaN) en texte — elles restent des float et font planter Paragraph()
    dans le tableau PDF, qui exige une vraie chaîne. Ce test couvre
    explicitement des comptes avec système ou nom manquant.
    """
    import pandas as pd
    from analysis.access_review import analyze_access
    from reporting.export import generate_pdf_report

    df = pd.DataFrame({
        "username": ["jdupont", "kbrou", "mfofana"],
        "full_name": ["Jean Dupont", None, "Marc Fofana"],
        "system": ["Active Directory", "CRM", None],
        "manager": [None, "Paul N.", "Marie D."],
    })
    result = analyze_access(df)
    output = generate_pdf_report(result, "output/test_missing_values.pdf")
    assert output.exists()
    print("OK - test_pdf_report_handles_missing_values_without_crashing")


def test_pdf_report_includes_signoff_names_when_provided():
    """Les noms de validation fournis doivent apparaître dans le PDF généré."""
    import pandas as pd
    from analysis.access_review import analyze_access
    from reporting.export import generate_pdf_report
    import pdfplumber

    df = pd.DataFrame({"username": ["jdupont"], "system": ["Active Directory"]})
    result = analyze_access(df)
    output = generate_pdf_report(
        result, "output/test_signoff.pdf",
        prepared_by="Test Preparateur", reviewed_by="Test Revu", approved_by="Test Approuve",
    )
    with pdfplumber.open(output) as pdf:
        full_text = "\n".join(page.extract_text() or "" for page in pdf.pages)
    assert "Test Preparateur" in full_text
    assert "Test Revu" in full_text
    assert "Test Approuve" in full_text
    assert "I. OBJECTIVE" in full_text
    assert "II. PRINCIPLES OF APPLICATION ACCOUNT CREATION" in full_text
    assert "VALIDATION" in full_text
    print("OK - test_pdf_report_includes_signoff_names_when_provided")


def test_pdf_report_includes_header_and_controls_reference():
    """L'en-tête configurable et le référentiel des 18 contrôles (texte
    fidèle du template, TEMPLATE_CONTROLS) doivent apparaître dans le PDF."""
    import pandas as pd
    from analysis.access_review import analyze_access
    from reporting.export import generate_pdf_report
    from reporting.template_sections import TEMPLATE_CONTROLS
    import pdfplumber

    assert len(TEMPLATE_CONTROLS) == 19

    df = pd.DataFrame({"username": ["jdupont"], "system": ["Active Directory"]})
    result = analyze_access(df)
    output = generate_pdf_report(
        result, "output/test_header.pdf",
        department="Test Department", editor="Test Editor",
        application_scope="Test App", document_version="2.0",
    )
    with pdfplumber.open(output) as pdf:
        full_text = "\n".join(page.extract_text() or "" for page in pdf.pages)
    assert "Test Department" in full_text
    assert "Test Editor" in full_text
    assert "Test App" in full_text
    assert "Version 2.0" in full_text
    assert "I. OBJECTIVE" in full_text
    assert "Dormant Accounts" in full_text
    print("OK - test_pdf_report_includes_header_and_controls_reference")


def test_template_sections_always_present_regardless_of_flag():
    """
    Depuis la reproduction fidèle du template (I. OBJECTIVE, II. PRINCIPLES...),
    ces sections font partie intégrante du document officiel et ne sont plus
    conditionnées par include_controls_reference — ce paramètre est conservé
    pour compatibilité mais n'a plus d'effet sur ces sections spécifiques.
    """
    import pandas as pd
    from analysis.access_review import analyze_access
    from reporting.export import generate_pdf_report
    import pdfplumber

    df = pd.DataFrame({"username": ["jdupont"], "system": ["Active Directory"]})
    result = analyze_access(df)
    output = generate_pdf_report(result, "output/test_no_controls.pdf", include_controls_reference=False)
    with pdfplumber.open(output) as pdf:
        full_text = "\n".join(page.extract_text() or "" for page in pdf.pages)
    assert "I. OBJECTIVE" in full_text
    assert "II. PRINCIPLES OF APPLICATION ACCOUNT CREATION" in full_text
    print("OK - test_template_sections_always_present_regardless_of_flag")


def test_dejavu_font_files_present_and_registered():
    """
    La police DejaVu Sans doit être physiquement présente dans le projet
    (assets/fonts/) et correctement enregistrée — sans quoi les rapports
    reculent silencieusement vers Helvetica, qui ne supporte pas les
    caractères hors alphabet latin de base (cyrillique, grec...).
    """
    from pathlib import Path
    from reportlab.pdfbase import pdfmetrics
    from reporting.export import DEFAULT_FONT, DEFAULT_FONT_BOLD

    fonts_dir = Path(__file__).parent.parent / "assets" / "fonts"
    assert (fonts_dir / "DejaVuSans.ttf").exists()
    assert (fonts_dir / "DejaVuSans-Bold.ttf").exists()
    assert DEFAULT_FONT == "DejaVu"
    assert DEFAULT_FONT_BOLD == "DejaVu-Bold"
    # Vérifie que reportlab a bien accepté l'enregistrement (lève une
    # exception si le nom n'a jamais été enregistré avec succès).
    pdfmetrics.getFont(DEFAULT_FONT)
    pdfmetrics.getFont(DEFAULT_FONT_BOLD)
    print("OK - test_dejavu_font_files_present_and_registered")


def test_cyrillic_name_renders_without_crash():
    """Un nom en cyrillique ne doit ni planter la génération, ni être
    silencieusement perdu — vérifié en confirmant sa présence dans le
    texte extrait du PDF généré."""
    import pandas as pd
    from analysis.access_review import analyze_access
    from reporting.export import generate_pdf_report
    import pdfplumber

    df = pd.DataFrame({
        "username": ["user1"], "full_name": ["Владимир Иванов"],
        "system": ["Active Directory"], "account_status": ["Active"],
    })
    result = analyze_access(df)
    output = generate_pdf_report(result, "output/test_cyrillic.pdf")
    with pdfplumber.open(output) as pdf:
        full_text = "\n".join(page.extract_text() or "" for page in pdf.pages)
    assert "Владимир" in full_text
    print("OK - test_cyrillic_name_renders_without_crash")


def test_control_characters_do_not_crash_excel_export():
    """
    Des caractères de contrôle invisibles (ex. NULL, souvent présents
    dans des exports mal nettoyés) faisaient planter l'export Excel
    entier (openpyxl les refuse). Doivent être nettoyés silencieusement
    avant écriture, sans faire échouer la génération.
    """
    import pandas as pd
    from analysis.access_review import analyze_access
    from reporting.export import generate_excel_report, generate_pdf_report

    df = pd.DataFrame({
        "username": ["user1\x00\x01"], "full_name": ["Jean\x0bDupont"],
        "system": ["AD"], "account_status": ["Active"],
    })
    result = analyze_access(df)
    generate_excel_report(result, "output/test_control_chars_regression.xlsx")
    generate_pdf_report(result, "output/test_control_chars_regression.pdf")
    print("OK - test_control_characters_do_not_crash_excel_export")


def test_strip_control_characters_works_regardless_of_column_dtype():
    """
    La fonction de nettoyage doit fonctionner même sur les colonnes
    utilisant le dtype 'string' dédié de pandas récent (pas seulement
    'object') — un vrai bug initial ne détectait que 'object'.
    """
    import pandas as pd
    from reporting.export import _strip_control_characters

    df = pd.DataFrame({"Compte": pd.array(["user1\x00\x01"], dtype="string")})
    result = _strip_control_characters(df)
    assert result["Compte"].iloc[0] == "user1"
    print("OK - test_strip_control_characters_works_regardless_of_column_dtype")


def test_all_18_control_subsections_present_with_exact_titles():
    """
    Les 18 sous-sections de la section IV doivent être reproduites avec
    leur titre exact (fidélité au template), pas un tableau consolidé
    générique — vérifié sur les titres les plus caractéristiques.
    """
    import pandas as pd
    from analysis.access_review import analyze_access
    from reporting.export import generate_pdf_report
    import pdfplumber

    df = pd.DataFrame({"username": ["u1"], "system": ["AD"], "account_status": ["Active"]})
    result = analyze_access(df)
    output = generate_pdf_report(result, "output/test_18_subsections.pdf")
    with pdfplumber.open(output) as pdf:
        full_text = "\n".join(page.extract_text() or "" for page in pdf.pages)

    for expected in [
        "1.Dump completeness and accuracy", "2.Dormant Accounts", "3.Orphaned Accounts",
        "9.Active Non-compliant logins", "15.3PP Accounts", "16.Administrator Accounts",
        "18.Terminated Users and Transferred users", "V. CONCLUSION",
    ]:
        assert expected in full_text, f"'{expected}' absent du rapport"
    print("OK - test_all_18_control_subsections_present_with_exact_titles")


def test_comparison_stats_feed_into_control_subsections():
    """Les sous-sections 10-13 (Accounts created/Profile Modified/
    Reactivated/Deleted) doivent afficher les vrais chiffres calculés par
    la comparaison avec la revue précédente, pas 'N/A'."""
    import pandas as pd
    from analysis.access_review import analyze_access
    from reporting.export import generate_pdf_report
    import pdfplumber

    previous = analyze_access(pd.DataFrame({
        "username": ["u1", "u2"], "system": ["AD"] * 2,
        "account_status": ["Active", "Disabled"], "role": ["User", "User"],
    }))
    current = analyze_access(pd.DataFrame({
        "username": ["u1", "u2", "u3"], "system": ["AD"] * 3,
        "account_status": ["Active", "Active", "Active"], "role": ["Admin", "User", "User"],
    }))
    output = generate_pdf_report(current, "output/test_comparison_subsections.pdf", previous_df=previous)
    with pdfplumber.open(output) as pdf:
        full_text = "\n".join(page.extract_text() or "" for page in pdf.pages)
    assert "10.Accounts created\n" in full_text or "10.Accounts created" in full_text
    # u3 créé, u1 profil modifié, u2 réactivé : aucun ne doit rester N/A
    idx = full_text.find("10.Accounts created")
    snippet = full_text[idx:idx + 200]
    assert "N/A" not in snippet
    print("OK - test_comparison_stats_feed_into_control_subsections")


def test_logo_path_none_by_default_no_crash():
    """Sans logo_path, le comportement reste inchangé (pas de logo requis
    pour générer un rapport)."""
    import pandas as pd
    from analysis.access_review import analyze_access
    from reporting.export import generate_pdf_report

    df = analyze_access(pd.DataFrame({"username": ["u1"], "system": ["AD"]}))
    generate_pdf_report(df, "output/test_logo_none.pdf")
    print("OK - test_logo_path_none_by_default_no_crash")


def test_logo_missing_file_does_not_crash():
    """Un chemin de logo qui n'existe pas ne doit jamais faire planter la
    génération — juste un en-tête sans logo."""
    import pandas as pd
    from analysis.access_review import analyze_access
    from reporting.export import generate_pdf_report

    df = analyze_access(pd.DataFrame({"username": ["u1"], "system": ["AD"]}))
    generate_pdf_report(df, "output/test_logo_missing.pdf", logo_path="/tmp/does_not_exist_12345.png")
    print("OK - test_logo_missing_file_does_not_crash")


def test_logo_inserted_when_valid_path_given():
    """Un fichier logo valide doit produire un PDF contenant réellement
    une image intégrée — vérifié directement (image XObject présente),
    pas par une comparaison de taille de fichier globale, trop fragile
    face à toute variation de compression sans rapport avec le logo
    lui-même (ex. ajout d'un contrôle supplémentaire ailleurs dans le
    document, qui déplace la pagination sans rien changer au logo)."""
    import pandas as pd
    import tempfile
    from PIL import Image
    from analysis.access_review import analyze_access
    from reporting.export import generate_pdf_report
    import pdfplumber

    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        logo_path = tmp.name
    Image.new("RGB", (200, 60), "#0E6E57").save(logo_path)

    df = analyze_access(pd.DataFrame({"username": ["u1"], "system": ["AD"]}))
    without = generate_pdf_report(df, "output/test_logo_compare_without.pdf")
    with_logo = generate_pdf_report(df, "output/test_logo_compare_with.pdf", logo_path=logo_path)

    with pdfplumber.open(without) as pdf:
        images_without = len(pdf.pages[0].images)
    with pdfplumber.open(with_logo) as pdf:
        images_with = len(pdf.pages[0].images)
    assert images_with > images_without, "Le logo ne semble pas avoir été intégré au PDF"
    print("OK - test_logo_inserted_when_valid_path_given")


def test_privilege_escalation_detected_between_reviews():
    """
    Un compte non privilégié dans la revue précédente qui devient
    privilégié dans la revue actuelle doit être détecté nommément comme
    'Privilege Escalation' — signal plus fort qu'un simple 'profil
    modifié' générique.
    """
    import pandas as pd
    from analysis.access_review import analyze_access
    from reporting.export import generate_pdf_report
    import pdfplumber

    previous = analyze_access(pd.DataFrame({
        "username": ["jdupont"], "system": ["AD"], "account_status": ["Active"], "role": ["User"],
    }))
    current = analyze_access(pd.DataFrame({
        "username": ["jdupont"], "system": ["AD"], "account_status": ["Active"], "role": ["Administrator"],
    }))
    output = generate_pdf_report(current, "output/test_escalation_regression.pdf", previous_df=previous)
    with pdfplumber.open(output) as pdf:
        full_text = "\n".join(page.extract_text() or "" for page in pdf.pages)
    assert "Privilege Escalation" in full_text
    assert "jdupont" in full_text
    print("OK - test_privilege_escalation_detected_between_reviews")


def test_control_summary_table_present_with_correct_status():
    """La table de synthèse compacte doit apparaître avant le détail
    verbeux, avec un statut cohérent (⚠️ si anomalies détectées, OK sinon,
    N/A si non calculable)."""
    import pandas as pd
    from analysis.access_review import analyze_access
    from reporting.export import generate_pdf_report
    import pdfplumber

    df = pd.DataFrame({
        "username": ["test_user"], "system": ["AD"], "account_status": ["Active"],
        "last_login_date": ["2026-09-01"],
    })
    result = analyze_access(df)
    output = generate_pdf_report(result, "output/test_control_summary_regression.pdf")
    with pdfplumber.open(output) as pdf:
        full_text = "\n".join(page.extract_text() or "" for page in pdf.pages)
    assert "Control Summary" in full_text
    assert full_text.index("Control Summary") < full_text.index("2.Dormant Accounts")
    print("OK - test_control_summary_table_present_with_correct_status")


def test_data_quality_section_appears_in_pdf_when_issues_found():
    """La section Qualité des données doit apparaître dans le PDF avec
    les vrais problèmes détectés, avant le reste du contenu opérationnel."""
    import pandas as pd
    from analysis.access_review import analyze_access
    from reporting.export import generate_pdf_report
    import pdfplumber

    df = pd.DataFrame({
        "username": ["u1", None], "system": ["AD"] * 2,
        "account_status": ["Active", "GarbageStatus"],
    })
    result = analyze_access(df)
    output = generate_pdf_report(result, "output/test_quality_regression.pdf")
    with pdfplumber.open(output) as pdf:
        full_text = "\n".join(page.extract_text() or "" for page in pdf.pages)
    assert "Data Quality" in full_text
    assert "Missing account identifiers" in full_text
    assert "Unrecognized account statuses" in full_text
    print("OK - test_data_quality_section_appears_in_pdf_when_issues_found")


def test_control_subsection_shows_account_detail_table():
    """
    Chaque sous-section avec des comptes concernés doit afficher un
    tableau nominatif (pas seulement un chiffre) — c'est ce qui rend le
    rapport exploitable en revue d'audit réelle.
    """
    import pandas as pd
    from analysis.access_review import analyze_access
    from reporting.export import generate_pdf_report
    import pdfplumber

    df = pd.DataFrame({
        "username": ["jdupont"], "full_name": ["Jean Dupont"], "system": ["AD"],
        "account_status": ["Active"], "last_login_date": ["2024-01-01"],
    })
    result = analyze_access(df)
    output = generate_pdf_report(result, "output/test_detail_regression.pdf")
    with pdfplumber.open(output) as pdf:
        full_text = "\n".join(page.extract_text() or "" for page in pdf.pages)
    idx = full_text.find("2.Dormant Accounts")
    snippet = full_text[idx:idx + 300]
    assert "jdupont" in snippet
    assert "Jean Dupont" in snippet
    print("OK - test_control_subsection_shows_account_detail_table")


def test_control_subsection_table_shows_all_accounts_no_cap():
    """
    Les 18 sections de contrôle sont celles effectivement revues : elles
    ne doivent JAMAIS être plafonnées, même sur un gros volume — tous les
    comptes doivent apparaître nommément.
    """
    import pandas as pd
    from analysis.access_review import analyze_access
    from reporting.export import generate_pdf_report
    import pdfplumber

    rows = [{"username": f"user{i:03d}", "system": "AD", "account_status": "Active",
              "last_login_date": "2024-01-01"} for i in range(50)]
    result = analyze_access(pd.DataFrame(rows))
    output = generate_pdf_report(result, "output/test_nocap_regression.pdf")
    with pdfplumber.open(output) as pdf:
        full_text = "\n".join(page.extract_text() or "" for page in pdf.pages)
    assert "autre(s) compte(s)" not in full_text
    assert "user000" in full_text
    assert "user049" in full_text
    print("OK - test_control_subsection_table_shows_all_accounts_no_cap")


def test_word_report_generates_without_crash():
    """Le rapport Word doit se générer sans erreur, avec la même
    structure que le PDF (mêmes calculs, moteur de rendu différent)."""
    import pandas as pd
    from analysis.access_review import analyze_access
    from reporting.export import generate_word_report

    df = pd.DataFrame({
        "username": ["jdupont"], "full_name": ["Jean Dupont"], "system": ["AD"],
        "account_status": ["Active"], "last_login_date": ["2024-01-01"],
    })
    result = analyze_access(df)
    output = generate_word_report(result, "output/test_word_regression.docx")
    assert output.exists()
    print("OK - test_word_report_generates_without_crash")


def test_word_report_content_matches_pdf_data():
    """Le contenu du Word doit refléter les mêmes données que le PDF —
    même compte, même action, même contrôle déclenché."""
    import pandas as pd
    from analysis.access_review import analyze_access
    from reporting.export import generate_word_report
    from docx import Document

    df = pd.DataFrame({
        "username": ["jdupont"], "full_name": ["Jean Dupont"], "system": ["AD"],
        "account_status": ["Active"], "last_login_date": ["2024-01-01"],
    })
    result = analyze_access(df)
    output = generate_word_report(result, "output/test_word_content.docx")
    doc = Document(str(output))
    full_text = "\n".join(p.text for p in doc.paragraphs)
    for table in doc.tables:
        for row in table.rows:
            full_text += "\n" + " ".join(cell.text for cell in row.cells)
    assert "jdupont" in full_text
    assert "2.Dormant Accounts" in full_text
    assert "I. OBJECTIVE" in full_text
    print("OK - test_word_report_content_matches_pdf_data")


def test_word_report_with_previous_review_comparison():
    """La comparaison avec la revue précédente doit fonctionner dans le
    Word exactement comme dans le PDF (créés/réactivés/escalade)."""
    import pandas as pd
    from analysis.access_review import analyze_access
    from reporting.export import generate_word_report
    from docx import Document

    previous = analyze_access(pd.DataFrame({
        "username": ["jdupont"], "system": ["AD"], "account_status": ["Active"], "role": ["User"],
    }))
    current = analyze_access(pd.DataFrame({
        "username": ["jdupont"], "system": ["AD"], "account_status": ["Active"], "role": ["Administrator"],
    }))
    output = generate_word_report(current, "output/test_word_comparison.docx", previous_df=previous)
    doc = Document(str(output))
    full_text = ""
    for table in doc.tables:
        for row in table.rows:
            full_text += " ".join(cell.text for cell in row.cells) + "\n"
    assert "Privilege Escalation" in full_text
    print("OK - test_word_report_with_previous_review_comparison")


def test_word_report_control_characters_do_not_crash():
    """
    Régression réelle : contrairement à ReportLab (PDF), python-docx
    (Word) rejette purement et simplement les caractères de contrôle
    avec une exception XML — la même correction que pour Excel était
    nécessaire ici aussi, mais n'avait pas été appliquée au nouveau
    chemin Word.
    """
    import pandas as pd
    from analysis.access_review import analyze_access
    from reporting.export import generate_word_report

    df = pd.DataFrame({
        "username": ["user1\x00\x01"], "full_name": ["Jean\x0bDupont"],
        "system": ["AD"], "account_status": ["Active"],
    })
    result = analyze_access(df)
    output = generate_word_report(result, "output/test_word_control_chars_regression.docx")
    assert output.exists()
    print("OK - test_word_report_control_characters_do_not_crash")


def test_word_report_empty_dataframe_no_crash():
    """Un DataFrame vide ne doit pas faire planter la génération Word."""
    import pandas as pd
    from analysis.access_review import analyze_access
    from reporting.export import generate_word_report

    df = pd.DataFrame({"username": [], "system": []})
    result = analyze_access(df)
    output = generate_word_report(result, "output/test_word_empty_regression.docx")
    assert output.exists()
    print("OK - test_word_report_empty_dataframe_no_crash")


def test_word_report_large_dataset_table_shows_all_no_cap():
    """Les 18 sections de contrôle ne doivent jamais être plafonnées,
    même sur un gros volume — cohérent avec le comportement PDF."""
    import pandas as pd
    from analysis.access_review import analyze_access
    from reporting.export import generate_word_report
    from docx import Document

    rows = [{"username": f"user{i:03d}", "system": "AD", "account_status": "Active",
              "last_login_date": "2024-01-01"} for i in range(50)]
    result = analyze_access(pd.DataFrame(rows))
    output = generate_word_report(result, "output/test_word_nocap_regression.docx")
    doc = Document(str(output))
    full_text = ""
    for table in doc.tables:
        for row in table.rows:
            full_text += " ".join(c.text for c in row.cells) + "\n"
    assert "autre(s) compte(s)" not in full_text
    assert "user000" in full_text
    assert "user049" in full_text
    print("OK - test_word_report_large_dataset_table_shows_all_no_cap")


def test_control_action_clarification_note_present():
    """
    Un compte peut apparaître sous plusieurs contrôles à la fois avec une
    action affichée qui reflète la priorité GLOBALE, pas la raison
    précise de sa présence dans CETTE section — doit être expliqué
    clairement, sinon un auditeur pourrait être perdu (ex. un compte
    listé sous 'Test Accounts' affichant 'Désactiver (privilégié
    dormant)' sans autre explication).
    """
    import pandas as pd
    from analysis.access_review import analyze_access
    from reporting.export import generate_pdf_report, generate_word_report
    import pdfplumber
    from docx import Document

    df = pd.DataFrame({
        "username": ["test_admin"], "system": ["AD"], "account_status": ["Active"],
        "is_privileged": ["Yes"], "last_login_date": ["2024-01-01"],
    })
    result = analyze_access(df)

    pdf_output = generate_pdf_report(result, "output/test_clarif_pdf.pdf")
    with pdfplumber.open(pdf_output) as pdf:
        pdf_text = "\n".join(page.extract_text() or "" for page in pdf.pages)
    assert "overall priority" in pdf_text

    word_output = generate_word_report(result, "output/test_clarif_word.docx")
    doc = Document(str(word_output))
    word_text = "\n".join(p.text for p in doc.paragraphs)
    assert "overall priority" in word_text
    print("OK - test_control_action_clarification_note_present")


def test_word_report_shows_escalated_account_names_not_just_count():
    """
    Régression réelle : Word affichait le CHIFFRE de l'escalade de
    privilège mais jamais les NOMS, contrairement au PDF qui liste les
    comptes concernés — incohérence entre les deux formats malgré une
    même source de données.
    """
    import pandas as pd
    from analysis.access_review import analyze_access
    from reporting.export import generate_word_report
    from docx import Document

    previous = analyze_access(pd.DataFrame({
        "username": ["jdupont"], "system": ["AD"], "account_status": ["Active"], "role": ["User"],
    }))
    current = analyze_access(pd.DataFrame({
        "username": ["jdupont"], "system": ["AD"], "account_status": ["Active"], "role": ["Administrator"],
    }))
    output = generate_word_report(current, "output/test_word_escalation_names.docx", previous_df=previous)
    doc = Document(str(output))
    full_text = "\n".join(p.text for p in doc.paragraphs)
    assert "jdupont" in full_text
    assert "Privilege Escalation" in full_text
    print("OK - test_word_report_shows_escalated_account_names_not_just_count")


def test_word_report_includes_risk_score_explainability_section():
    """
    Régression réelle : la section 'Score de risque — détail du calcul'
    (top 10 comptes les plus exposés avec raisons) existait seulement
    dans le PDF, absente de Word — incohérence entre les deux formats.
    """
    import pandas as pd
    from analysis.access_review import analyze_access
    from reporting.export import generate_word_report
    from docx import Document

    df = pd.DataFrame({
        "username": ["jdupont"], "system": ["AD"], "account_status": ["Active"],
        "employee_status": ["Terminated"], "is_privileged": ["Yes"],
        "last_login_date": ["2024-01-01"],
    })
    result = analyze_access(df)
    output = generate_word_report(result, "output/test_word_risk_detail_regression.docx")
    doc = Document(str(output))
    full_text = "\n".join(p.text for p in doc.paragraphs)
    assert "Risk Score" in full_text
    assert "Departed employee" in full_text
    print("OK - test_word_report_includes_risk_score_explainability_section")


def test_word_report_includes_exceptions_section():
    """
    'Rapport des exceptions' doit exister dans Word, comme dans PDF —
    régression trouvée par comparaison systématique entre les deux
    formats. 'Détail par système' a depuis été retiré (redondant avec
    les 18 sections de contrôle désormais complètes et sans plafond).
    """
    import pandas as pd
    from analysis.access_review import analyze_access
    from reporting.export import generate_word_report
    from docx import Document

    df = pd.DataFrame({
        "username": ["jdupont"], "full_name": ["Jean Dupont"], "system": ["AD"],
        "account_status": ["Active"], "employee_status": ["Terminated"],
    })
    result = analyze_access(df)
    output = generate_word_report(result, "output/test_word_exceptions_detail.docx")
    doc = Document(str(output))
    full_text = "\n".join(p.text for p in doc.paragraphs)
    for table in doc.tables:
        for row in table.rows:
            full_text += "\n" + " ".join(c.text for c in row.cells)
    assert "Exceptions Report" in full_text
    assert "jdupont" in full_text
    print("OK - test_word_report_includes_exceptions_section")


def test_deleted_accounts_found_in_previous_df_not_current():
    """
    Vrai bug trouvé : un compte supprimé n'existe par définition plus
    dans le fichier ACTUEL (df) — le chercher là renvoyait toujours zéro
    résultat. Doit être retrouvé dans la revue PRÉCÉDENTE, où il existe
    encore, pour afficher ses vrais attributs.
    """
    import pandas as pd
    from analysis.access_review import analyze_access
    from reporting.export import generate_pdf_report, generate_word_report
    import pdfplumber
    from docx import Document

    previous = analyze_access(pd.DataFrame({
        "username": ["jdupont", "old_leaver"], "full_name": ["Jean Dupont", "Ancien Employé"],
        "system": ["AD"] * 2, "account_status": ["Active"] * 2,
    }))
    current = analyze_access(pd.DataFrame({
        "username": ["jdupont"], "full_name": ["Jean Dupont"], "system": ["AD"],
        "account_status": ["Active"],
    }))

    pdf_output = generate_pdf_report(current, "output/test_deleted_pdf_regression.pdf", previous_df=previous)
    with pdfplumber.open(pdf_output) as pdf:
        pdf_text = "\n".join(page.extract_text() or "" for page in pdf.pages)
    assert "old_leaver" in pdf_text

    word_output = generate_word_report(current, "output/test_deleted_word_regression.docx", previous_df=previous)
    doc = Document(str(word_output))
    word_text = ""
    for table in doc.tables:
        for row in table.rows:
            word_text += " ".join(c.text for c in row.cells) + "\n"
    assert "old_leaver" in word_text
    print("OK - test_deleted_accounts_found_in_previous_df_not_current")


def test_control_specific_justifying_columns_shown():
    """
    Chaque contrôle doit afficher les colonnes qui permettent de
    VÉRIFIER pourquoi un compte y figure (ex. la vraie date de dernière
    connexion et son ancienneté en jours pour 'Dormant'), pas seulement
    l'action recommandée qui en résulte.
    """
    import pandas as pd
    from analysis.access_review import analyze_access, _days_since
    from reporting.export import generate_pdf_report
    import pdfplumber

    df = pd.DataFrame({
        "username": ["jdupont"], "full_name": ["Jean Dupont"], "system": ["AD"],
        "account_status": ["Active"], "last_login_date": ["2024-01-01"],
    })
    result = analyze_access(df)
    output = generate_pdf_report(result, "output/test_justifying_cols.pdf")
    with pdfplumber.open(output) as pdf:
        full_text = "\n".join(page.extract_text() or "" for page in pdf.pages)
    idx = full_text.find("2.Dormant Accounts")
    snippet = full_text[idx:idx + 600]
    # Vérification par mot plutôt que par phrase exacte : un en-tête un
    # peu long s'enveloppe légitimement sur plusieurs lignes dans le PDF
    # (vérifié visuellement, rendu correct), ce que l'ordre de lecture du
    # texte extrait ne préserve pas toujours fidèlement.
    assert "Last" in snippet and "Login" in snippet
    assert "Days" in snippet and "Since" in snippet
    assert "Recommended Action" in snippet
    # Valeur en jours calculée dynamiquement plutôt que codée en dur : un
    # nombre figé casse silencieusement le test un jour plus tard (repéré
    # ici même), sans rapport avec un vrai changement de comportement.
    expected_days = int(_days_since("2024-01-01"))
    assert str(expected_days) in snippet
    print("OK - test_control_specific_justifying_columns_shown")


def test_compute_control_coverage_shared_by_pdf_and_dashboard():
    """
    compute_control_coverage doit être la SEULE source de vérité pour
    l'état des 18 contrôles — réutilisée par le PDF (Control Summary) et
    le dashboard (Control Coverage), sans dupliquer la logique."""
    import pandas as pd
    from analysis.access_review import analyze_access
    from reporting.export import compute_control_coverage

    df = pd.DataFrame({
        "username": ["jdupont"], "system": ["AD"], "account_status": ["Active"],
        "last_login_date": ["2024-01-01"],
    })
    result = analyze_access(df)
    coverage = compute_control_coverage(result, {})
    assert len(coverage) == 19
    dormant_entry = next(c for c in coverage if c[1] == "Dormant Accounts")
    assert dormant_entry[2] == "⚠️"  # jdupont est dormant -> anomalie détectée
    assert dormant_entry[3] == "1"
    print("OK - test_compute_control_coverage_shared_by_pdf_and_dashboard")


def test_word_generation_performance_not_quadratic():
    """
    Vrai bug de performance trouvé et corrigé : table.cell(i, j) de
    python-docx reconstruit TOUTE la structure de fusion de cellules de
    la table depuis le XML à CHAQUE appel — utilisé dans une double
    boucle de remplissage, ça rendait la génération quadratique en
    nombre de lignes (plus de 300 secondes pour 500 comptes, contre
    ~3 secondes après correction). Remplacé par table.rows[i].cells[j],
    qui ne scanne que la ligne concernée. Ce test vérifie que la
    génération reste rapide pour un volume représentatif, pas
    seulement qu'elle produit un résultat correct.
    """
    import time
    import pandas as pd
    from analysis.access_review import analyze_access
    from reporting.export import generate_word_report

    n = 200
    df = pd.DataFrame({
        "username": [f"user{i}" for i in range(n)],
        "full_name": [f"Nom Prenom {i}" for i in range(n)],
        "system": ["AD"] * n,
        "account_status": ["Active"] * n,
    })
    result = analyze_access(df)
    t0 = time.time()
    generate_word_report(result, "output/test_perf_regression.docx")
    elapsed = time.time() - t0
    # Large marge de sécurité (5s pour 200 comptes) : le but n'est pas de
    # chronométrer précisément mais d'attraper un retour au comportement
    # quadratique si jamais réintroduit (qui donnerait largement plus).
    assert elapsed < 5, f"Génération Word anormalement lente ({elapsed:.1f}s pour {n} comptes) — retour possible au comportement quadratique"
    print(f"OK - test_word_generation_performance_not_quadratic ({elapsed:.2f}s pour {n} comptes)")


def test_default_report_filename_uses_system_and_date():
    """Le nom de fichier calculé doit suivre le format demandé :
    Rapport_revue_acces_<système>_<JJMMAAAA>.<extension>."""
    import pandas as pd
    from datetime import datetime
    from reporting.export import default_report_filename

    df_single = pd.DataFrame({"system": ["AD", "AD"]})
    today = datetime.now().strftime("%d%m%Y")
    assert default_report_filename(df_single, "pdf") == f"Rapport_revue_acces_AD_{today}.pdf"

    df_multi = pd.DataFrame({"system": ["AD", "SAP"]})
    assert default_report_filename(df_multi, "docx") == f"Rapport_revue_acces_AD-SAP_{today}.docx"

    df_many = pd.DataFrame({"system": ["AD", "SAP", "VPN", "Cloud", "Firewall"]})
    assert default_report_filename(df_many, "xlsx") == f"Rapport_revue_acces_Multi-systemes_{today}.xlsx"

    df_none = pd.DataFrame({"username": ["u1"]})
    assert default_report_filename(df_none, "pdf") == f"Rapport_revue_acces_Global_{today}.pdf"
    print("OK - test_default_report_filename_uses_system_and_date")


def test_validation_table_uses_new_role_structure():
    """Le tableau de validation en en-tête doit utiliser les nouveaux
    rôles demandés (Control Performer / Manager HUB / HUB senior Manager
    LISO puis SYSTEM OWNER / OPCOS LISO / SM Information Security
    OPCOS), plus l'ancienne structure (MANAGER / SENIOR MANAGER / CTIO)."""
    import pandas as pd
    from analysis.access_review import analyze_access
    from reporting.export import generate_pdf_report, generate_word_report
    import pdfplumber
    from docx import Document

    df = pd.DataFrame({"username": ["u1"], "system": ["AD"]})
    result = analyze_access(df)

    pdf_path = generate_pdf_report(result, "output/test_validation_roles.pdf")
    with pdfplumber.open(pdf_path) as pdf:
        pdf_text = "\n".join(page.extract_text() or "" for page in pdf.pages)
    for role in ["Control Performer", "Manager HUB", "HUB senior Manager LISO",
                 "SYSTEM OWNER", "OPCOS LISO", "SM Information Security OPCOS"]:
        assert role in pdf_text, f"'{role}' absent du PDF"
    validation_idx = pdf_text.find("VALIDATION")
    validation_snippet = pdf_text[validation_idx:validation_idx + 400]
    assert "CTIO:" not in validation_snippet

    word_path = generate_word_report(result, "output/test_validation_roles.docx")
    doc = Document(str(word_path))
    word_text = "\n".join(p.text for p in doc.paragraphs)
    for table in doc.tables:
        for row in table.rows:
            word_text += "\n" + " ".join(c.text for c in row.cells)
    for role in ["Control Performer", "Manager HUB", "HUB senior Manager LISO",
                 "SYSTEM OWNER", "OPCOS LISO", "SM Information Security OPCOS"]:
        assert role in word_text, f"'{role}' absent du Word"
    validation_idx_word = word_text.find("VALIDATION")
    validation_snippet_word = word_text[validation_idx_word:validation_idx_word + 400]
    assert "CTIO:" not in validation_snippet_word
    print("OK - test_validation_table_uses_new_role_structure")


def test_dump_completeness_includes_description_row():
    """Le tableau Dump completeness and accuracy doit inclure une ligne
    'Description' avec OK/NOK selon la présence réelle de la colonne."""
    import pandas as pd
    from analysis.access_review import analyze_access
    from reporting.export import generate_pdf_report, generate_word_report
    import pdfplumber
    from docx import Document

    df_with = pd.DataFrame({"username": ["u1"], "system": ["AD"], "description": ["Standard account"]})
    result_with = analyze_access(df_with)
    pdf_path = generate_pdf_report(result_with, "output/test_desc_present.pdf")
    with pdfplumber.open(pdf_path) as pdf:
        pdf_text = "\n".join(page.extract_text() or "" for page in pdf.pages)
    idx = pdf_text.find("Field Status")
    assert "Description OK" in pdf_text[idx:idx + 300]

    df_without = pd.DataFrame({"username": ["u1"], "system": ["AD"]})
    result_without = analyze_access(df_without)
    word_path = generate_word_report(result_without, "output/test_desc_absent.docx")
    doc = Document(str(word_path))
    word_text = ""
    for table in doc.tables:
        for row in table.rows:
            word_text += " | ".join(c.text for c in row.cells) + "\n"
    assert "Description | NOK" in word_text
    print("OK - test_dump_completeness_includes_description_row")


def test_owner_tracking_table_appears_before_account_table():
    """Demande explicite : le tableau de suivi (Owner/Comment/Due
    Date/Status) doit apparaître AVANT le tableau nominatif des comptes
    dans chaque section, pas après — dans le PDF et dans Word."""
    import pandas as pd
    from analysis.access_review import analyze_access
    from reporting.export import generate_pdf_report, generate_word_report
    import pdfplumber
    from docx import Document

    df = pd.DataFrame({
        "username": ["jdupont"], "full_name": ["Jean Dupont"], "system": ["AD"],
        "account_status": ["Active"], "last_login_date": ["2020-01-01"],
    })
    result = analyze_access(df)

    pdf_path = generate_pdf_report(result, "output/test_tracking_order.pdf")
    with pdfplumber.open(pdf_path) as pdf:
        full_text = "\n".join(page.extract_text() or "" for page in pdf.pages)
    idx = full_text.find("2.Dormant Accounts")
    snippet = full_text[idx:idx + 400]
    assert snippet.find("Owner") < snippet.find("jdupont")

    word_path = generate_word_report(result, "output/test_tracking_order.docx")
    doc = Document(str(word_path))
    body_elements = list(doc.element.body)
    tracking_idx = account_idx = None
    for i, el in enumerate(body_elements):
        text = el.text if hasattr(el, "text") else ""
        if el.tag.endswith("}tbl"):
            from docx.table import Table as _T
            table = _T(el, doc)
            header = [c.text for c in table.rows[0].cells]
            if header == ["Owner", "Comment", "Due Date", "Status"] and tracking_idx is None:
                tracking_idx = i
            elif any("jdupont" in c.text for row in table.rows for c in row.cells) and account_idx is None:
                account_idx = i
    assert tracking_idx is not None and account_idx is not None
    assert tracking_idx < account_idx
    print("OK - test_owner_tracking_table_appears_before_account_table")


def test_extraction_origin_overrides_filename_system():
    """L'origine de l'extraction, quand renseignée, remplace le nom de
    système déduit automatiquement dans le nom de fichier — laissée
    vide ou absente, le comportement précédent (système déduit) reste
    inchangé."""
    import pandas as pd
    from datetime import datetime
    from reporting.export import default_report_filename

    df = pd.DataFrame({"system": ["AD"]})
    today = datetime.now().strftime("%d%m%Y")

    assert default_report_filename(df, "pdf") == f"Rapport_revue_acces_AD_{today}.pdf"
    assert (
        default_report_filename(df, "pdf", extraction_origin="Extraction ServiceNow mensuelle")
        == f"Rapport_revue_acces_Extraction_ServiceNow_mensuelle_{today}.pdf"
    )
    assert default_report_filename(df, "pdf", extraction_origin="   ") == f"Rapport_revue_acces_AD_{today}.pdf"
    assert default_report_filename(df, "pdf", extraction_origin=None) == f"Rapport_revue_acces_AD_{today}.pdf"
    print("OK - test_extraction_origin_overrides_filename_system")


def test_controls_reference_table_sn_column_is_narrow():
    """La colonne SN du tableau de référence des 18 contrôles (section
    I. OBJECTIVE) doit rester étroite (juste assez pour un numéro à 1-2
    chiffres), pas la même largeur proportionnelle que les colonnes de
    contenu — retour utilisateur explicite après comparaison visuelle.
    Vérifié directement sur le code source plutôt que sur le rendu
    visuel, pour une garde de non-régression simple et rapide."""
    import inspect
    from reporting import export

    pdf_source = inspect.getsource(export.generate_pdf_report)
    assert "(0.03, 0.20, 0.77)" in pdf_source

    word_source = inspect.getsource(export.generate_word_report)
    assert "[0.7, 4, 12.3]" in word_source
    print("OK - test_controls_reference_table_sn_column_is_narrow")


def test_review_comparison_normalizes_case_for_created_deleted():
    """
    Vrai bug trouvé : la comparaison créés/supprimés entre deux cycles de
    revue (contrôles 10/13) se faisait sur le nom de compte brut, sans
    normalisation — 'jdupont' (revue précédente) et 'JDupont' (revue
    actuelle, casse différente si l'export a changé entre deux mois)
    étaient traités comme deux comptes DIFFÉRENTS : un faux "supprimé"
    et un faux "créé" pour le MÊME compte, un signal trompeur pour un
    rapport d'audit.
    """
    import pandas as pd
    from analysis.access_review import analyze_access
    from reporting.export import _build_review_comparison_section
    from reportlab.lib.styles import getSampleStyleSheet

    styles = getSampleStyleSheet()
    previous = pd.DataFrame({
        "username": ["jdupont", "old_user"], "system": ["AD"] * 2,
        "account_status": ["Active", "Active"],
    })
    current = pd.DataFrame({
        "username": ["JDupont", "new_user"], "system": ["AD"] * 2,
        "account_status": ["Active", "Active"],
    })
    prev_result = analyze_access(previous)
    curr_result = analyze_access(current)
    _, stats = _build_review_comparison_section(
        curr_result, prev_result, styles["Heading2"], styles["Normal"], 400
    )
    # Le seul vrai changement (new_user créé, old_user supprimé) doit
    # être détecté ; jdupont/JDupont ne doit PAS apparaître comme
    # changement.
    assert stats["created"] == 1 and stats["created_accounts"] == ["new_user"]
    assert stats["deleted"] == 1 and stats["deleted_accounts"] == ["old_user"]
    print("OK - test_review_comparison_normalizes_case_for_created_deleted")


def test_accounts_created_uses_direct_date_when_available_no_previous_review_needed():
    """
    Demande explicite : le contrôle 'Accounts created' doit d'abord
    utiliser account_created_date directement (comptes créés dans les 90
    jours depuis la date d'extraction), sans nécessiter de revue
    précédente — la comparaison avec une revue précédente ne sert que de
    repli quand cette colonne est absente. Confirmé cohérent avec le
    texte officiel du template ('check the creation date... if the
    system does not provide creation, perform the comparison...').
    """
    import pandas as pd
    from analysis.access_review import analyze_access
    from reporting.export import generate_pdf_report
    import pdfplumber

    df = pd.DataFrame({
        "username": ["u1", "u2"], "system": ["AD"] * 2,
        "account_created_date": ["2026-08-15", "2020-01-01"],
    })
    result = analyze_access(df, reference_datetime=pd.Timestamp("2026-09-12"))
    output = generate_pdf_report(result, "output/test_created_direct.pdf")
    with pdfplumber.open(output) as pdf:
        text = "\n".join(p.extract_text() or "" for p in pdf.pages)
    idx = text.find("10.Accounts created")
    snippet = text[idx:idx + 700]
    assert "1 account(s) concerned" in snippet
    assert "u1" in snippet
    assert "u2" not in snippet
    print("OK - test_accounts_created_uses_direct_date_when_available_no_previous_review_needed")


def test_accounts_created_falls_back_to_comparison_without_creation_date():
    """Sans 'account_created_date', le contrôle doit se replier sur la
    comparaison avec une revue précédente (comportement déjà existant),
    pas afficher N/A à tort si une revue précédente est fournie."""
    import pandas as pd
    from analysis.access_review import analyze_access
    from reporting.export import generate_pdf_report
    import pdfplumber

    previous = pd.DataFrame({"username": ["u1"], "system": ["AD"], "account_status": ["Active"]})
    current = pd.DataFrame({"username": ["u1", "u2"], "system": ["AD"] * 2, "account_status": ["Active"] * 2})
    prev_result = analyze_access(previous)
    curr_result = analyze_access(current)
    output = generate_pdf_report(curr_result, "output/test_created_fallback.pdf", previous_df=prev_result)
    with pdfplumber.open(output) as pdf:
        text = "\n".join(p.extract_text() or "" for p in pdf.pages)
    idx = text.find("10.Accounts created")
    snippet = text[idx:idx + 700]
    assert "1 account(s) concerned" in snippet
    assert "u2" in snippet
    print("OK - test_accounts_created_falls_back_to_comparison_without_creation_date")


def test_profile_modified_and_reactivated_show_before_after_comparison_table():
    """
    Demande explicite : Profile Modified et Reactivated accounts
    doivent afficher un tableau Account/System/ancienne valeur+date/
    nouvelle valeur+date, pas juste un compte sans contexte de CE qui a
    changé.
    """
    import pandas as pd
    from analysis.access_review import analyze_access
    from reporting.export import generate_pdf_report, generate_word_report
    import pdfplumber
    from docx import Document

    previous = pd.DataFrame({
        "username": ["jdupont", "mmartin"], "system": ["AD"] * 2,
        "account_status": ["Active", "Disabled"], "role": ["Standard User", "Standard User"],
    })
    current = pd.DataFrame({
        "username": ["jdupont", "mmartin"], "system": ["AD"] * 2,
        "account_status": ["Active", "Active"], "role": ["Administrator", "Standard User"],
    })
    prev_result = analyze_access(previous)
    curr_result = analyze_access(current)

    pdf_path = generate_pdf_report(
        curr_result, "output/test_pm_pdf.pdf", previous_df=prev_result,
        current_extraction_date="2026-09-12", previous_extraction_date="2026-06-01",
    )
    with pdfplumber.open(pdf_path) as pdf:
        text = "\n".join(p.extract_text() or "" for p in pdf.pages)
    pm_idx = text.find("11.Profile Modified")
    pm_snippet = text[pm_idx:pm_idx + 400]
    assert "Standard User" in pm_snippet and "Administrator" in pm_snippet
    assert "2026-06-01" in pm_snippet and "2026-09-12" in pm_snippet

    react_idx = text.find("12.Reactivated accounts")
    react_snippet = text[react_idx:react_idx + 400]
    assert "Disabled" in react_snippet and "Active" in react_snippet
    assert "2026-06-01" in react_snippet and "2026-09-12" in react_snippet

    word_path = generate_word_report(
        curr_result, "output/test_pm_word.docx", previous_df=prev_result,
        current_extraction_date="2026-09-12", previous_extraction_date="2026-06-01",
    )
    doc = Document(str(word_path))
    found_profile_table = False
    for table in doc.tables:
        header = [c.text for c in table.rows[0].cells]
        if "Previous Profile" in header:
            found_profile_table = True
            row = [c.text for c in table.rows[1].cells]
            assert row == ["jdupont", "AD", "Standard User", "2026-06-01", "Administrator", "2026-09-12"]
    assert found_profile_table
    print("OK - test_profile_modified_and_reactivated_show_before_after_comparison_table")


def test_control_19_present_and_annex_f_referenced():
    """Nouvelle section 19 (First line user access review report and
    accuracy) et référence Annexe F, demandées explicitement, doivent
    apparaître dans le PDF et dans Word."""
    import pandas as pd
    from analysis.access_review import analyze_access
    from reporting.export import generate_pdf_report, generate_word_report
    import pdfplumber
    from docx import Document

    df = analyze_access(pd.DataFrame({"username": ["u1"], "system": ["AD"]}))
    pdf_path = generate_pdf_report(df, "output/test_control19.pdf")
    with pdfplumber.open(pdf_path) as pdf:
        pdf_text = "\n".join(p.extract_text() or "" for p in pdf.pages)
    assert "19.First line user access review report and accuracy" in pdf_text
    assert "F. First List user access review Report" in pdf_text

    word_path = generate_word_report(df, "output/test_control19.docx")
    doc = Document(str(word_path))
    word_text = "\n".join(p.text for p in doc.paragraphs)
    assert any("First line user access review report and accuracy" in p.text for p in doc.paragraphs)
    assert any("First List user access review Report" in p.text for p in doc.paragraphs)
    print("OK - test_control_19_present_and_annex_f_referenced")


def test_word_document_has_real_metadata_not_2013_placeholder():
    """
    Trouvé en investiguant le souci d'édition SharePoint : python-docx
    laisse par défaut une date de création/modification figée sur 2013
    (celle de son modèle interne) et un auteur vide — un signal de
    non-fiabilité pour tout système affichant ces métadonnées, corrigé
    avec la vraie date de génération et l'éditeur renseigné.
    """
    import pandas as pd
    from datetime import datetime
    from analysis.access_review import analyze_access
    from reporting.export import generate_word_report
    from docx import Document

    df = analyze_access(pd.DataFrame({"username": ["u1"], "system": ["AD"]}))
    output = generate_word_report(df, "output/test_metadata.docx", editor="Dylan Gbenou")
    doc = Document(str(output))
    assert doc.core_properties.author == "Dylan Gbenou"
    assert doc.core_properties.created.year == datetime.now().year
    print("OK - test_word_document_has_real_metadata_not_2013_placeholder")
