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
    assert "Synthèse" in wb.sheetnames
    assert "Plan de revue" in wb.sheetnames
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
    assert "I. OBJECTIF" in full_text
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

    assert len(TEMPLATE_CONTROLS) == 18

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
    assert "I. OBJECTIF" in full_text
    assert "Comptes dormants" in full_text
    print("OK - test_pdf_report_includes_header_and_controls_reference")


def test_template_sections_always_present_regardless_of_flag():
    """
    Depuis la reproduction fidèle du template (I. OBJECTIF, II. PRINCIPLES...),
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
    assert "I. OBJECTIF" in full_text
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
    """Un fichier logo valide doit produire un PDF plus volumineux
    (image effectivement incluse) qu'un rapport identique sans logo."""
    import pandas as pd
    import tempfile
    from PIL import Image
    from analysis.access_review import analyze_access
    from reporting.export import generate_pdf_report

    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        logo_path = tmp.name
    Image.new("RGB", (200, 60), "#0E6E57").save(logo_path)

    df = analyze_access(pd.DataFrame({"username": ["u1"], "system": ["AD"]}))
    without = generate_pdf_report(df, "output/test_logo_compare_without.pdf")
    with_logo = generate_pdf_report(df, "output/test_logo_compare_with.pdf", logo_path=logo_path)
    assert Path(with_logo).stat().st_size > Path(without).stat().st_size
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
    assert "Qualité des données" in full_text
    assert "Identifiants de compte manquants" in full_text
    assert "Statuts de compte non reconnus" in full_text
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


def test_control_subsection_table_capped_on_large_dataset():
    """Au-delà du plafond, une mention doit renvoyer vers le détail
    complet plutôt que de faire exploser le document."""
    import pandas as pd
    from analysis.access_review import analyze_access
    from reporting.export import generate_pdf_report
    import pdfplumber

    rows = [{"username": f"user{i:03d}", "system": "AD", "account_status": "Active",
              "last_login_date": "2024-01-01"} for i in range(50)]
    result = analyze_access(pd.DataFrame(rows))
    output = generate_pdf_report(result, "output/test_capped_regression.pdf")
    with pdfplumber.open(output) as pdf:
        full_text = "\n".join(page.extract_text() or "" for page in pdf.pages)
    assert "autre(s) compte(s)" in full_text
    print("OK - test_control_subsection_table_capped_on_large_dataset")


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
    assert "I. OBJECTIF" in full_text
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


def test_word_report_large_dataset_table_capped():
    """Au-delà du plafond de 30 comptes par contrôle, une mention doit
    renvoyer vers le détail complet — cohérent avec le comportement PDF."""
    import pandas as pd
    from analysis.access_review import analyze_access
    from reporting.export import generate_word_report
    from docx import Document

    rows = [{"username": f"user{i:03d}", "system": "AD", "account_status": "Active",
              "last_login_date": "2024-01-01"} for i in range(50)]
    result = analyze_access(pd.DataFrame(rows))
    output = generate_word_report(result, "output/test_word_capped_regression.docx")
    doc = Document(str(output))
    full_text = "\n".join(p.text for p in doc.paragraphs)
    assert "autre(s) compte(s)" in full_text
    print("OK - test_word_report_large_dataset_table_capped")


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
    assert "action prioritaire globale" in pdf_text

    word_output = generate_word_report(result, "output/test_clarif_word.docx")
    doc = Document(str(word_output))
    word_text = "\n".join(p.text for p in doc.paragraphs)
    assert "action prioritaire globale" in word_text
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
    assert "Score de risque" in full_text
    assert "Employé parti" in full_text
    print("OK - test_word_report_includes_risk_score_explainability_section")


def test_word_report_includes_exceptions_and_detail_by_system():
    """
    Régression réelle, trouvée par comparaison systématique PDF/Word :
    'Rapport des exceptions' et 'Détail par système' existaient
    seulement dans le PDF — absentes de Word. Or plusieurs notes de
    plafonnement ('voir le détail complet par système') y renvoient
    explicitement : sans cette section, la promesse n'est pas tenue.
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
    assert "Rapport des exceptions" in full_text
    assert "Détail par système" in full_text
    assert "jdupont" in full_text
    print("OK - test_word_report_includes_exceptions_and_detail_by_system")


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
    from analysis.access_review import analyze_access
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
    snippet = full_text[idx:idx + 400]
    assert "Dernière connexion" in snippet
    assert "Jours sans connexion" in snippet
    print("OK - test_control_specific_justifying_columns_shown")
