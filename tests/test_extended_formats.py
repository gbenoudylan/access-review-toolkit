"""
Tests des formats de fichiers étendus : JSON, XML, HTML, LDIF, PDF, ZIP.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from ingestion.ingest import load_file, IngestionError


def test_json_list_of_records(tmp_path):
    import json
    data = [
        {"username": "jkonan", "system": "Active Directory", "account_status": "Active", "employee_status": "Active"},
        {"username": "bafolabi", "system": "SAP", "account_status": "Active", "employee_status": "Terminated"},
    ]
    path = tmp_path / "test.json"
    path.write_text(json.dumps(data), encoding="utf-8")

    df = load_file(path)
    assert len(df) == 2
    assert "username" in df.columns
    print("OK - test_json_list_of_records")


def test_json_wrapped_in_key(tmp_path):
    import json
    data = {"total": 2, "results": [
        {"username": "mkeita", "system": "VPN", "account_status": "Active", "employee_status": "Active"},
    ]}
    path = tmp_path / "wrapped.json"
    path.write_text(json.dumps(data), encoding="utf-8")

    df = load_file(path)
    assert len(df) == 1
    print("OK - test_json_wrapped_in_key")


def test_xml_repeated_elements(tmp_path):
    content = """<?xml version="1.0"?>
<accounts>
  <account>
    <username>sagbato</username>
    <system>SIEM</system>
    <account_status>Active</account_status>
    <employee_status>Terminated</employee_status>
  </account>
  <account>
    <username>rzongo</username>
    <system>HRIS</system>
    <account_status>Active</account_status>
    <employee_status>Active</employee_status>
  </account>
</accounts>"""
    path = tmp_path / "test.xml"
    path.write_text(content, encoding="utf-8")

    df = load_file(path)
    assert len(df) == 2
    assert "username" in df.columns
    print("OK - test_xml_repeated_elements")


def test_html_table(tmp_path):
    content = """<html><body>
    <table>
    <tr><th>Username</th><th>System</th><th>Account Status</th><th>Employee Status</th></tr>
    <tr><td>dyao</td><td>Active Directory</td><td>Active</td><td>Terminated</td></tr>
    <tr><td>kboni</td><td>SAP</td><td>Active</td><td>Active</td></tr>
    </table>
    </body></html>"""
    path = tmp_path / "test.html"
    path.write_text(content, encoding="utf-8")

    df = load_file(path)
    assert len(df) == 2
    assert "username" in df.columns
    # Vérifie que l'en-tête (Username, System...) n'a pas été traité comme
    # une ligne de données par erreur (bug corrigé pendant le développement)
    assert "dyao" not in df.columns
    print("OK - test_html_table")


def test_ldif_with_useraccountcontrol_decoding(tmp_path):
    content = """dn: CN=Jean Konan,OU=Users,DC=mtn,DC=local
sAMAccountName: jkonan
cn: Jean Konan
mail: jkonan@mtn.example
userAccountControl: 512

dn: CN=Awa Doumbia,OU=Users,DC=mtn,DC=local
sAMAccountName: adoumbia
cn: Awa Doumbia
mail: adoumbia@mtn.example
userAccountControl: 514
"""
    path = tmp_path / "test.ldif"
    path.write_text(content, encoding="utf-8")

    df = load_file(path)
    assert len(df) == 2
    assert "username" in df.columns
    assert "system" in df.columns  # ajouté automatiquement (LDIF = un seul système)

    statuses = dict(zip(df["username"], df["account_status"]))
    assert statuses["jkonan"] == "Active"     # 512 = compte actif normal
    assert statuses["adoumbia"] == "Disabled"  # 514 = bit ACCOUNTDISABLE positionné
    print("OK - test_ldif_with_useraccountcontrol_decoding")


def test_pdf_table(tmp_path):
    from reportlab.lib.pagesizes import A4
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle
    from reportlab.lib import colors

    data = [
        ["Username", "System", "Account Status", "Employee Status"],
        ["ekacou", "SIEM ArcSight", "Active", "Active"],
        ["fbamba", "Office 365", "Active", "Terminated"],
    ]
    path = tmp_path / "test.pdf"
    doc = SimpleDocTemplate(str(path), pagesize=A4)
    table = Table(data)
    table.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.5, colors.grey)]))
    doc.build([table])

    df = load_file(path)
    assert len(df) == 2
    assert "username" in df.columns
    print("OK - test_pdf_table")


def test_zip_with_multiple_formats(tmp_path):
    """
    Le cas le plus complexe : une archive contenant plusieurs formats
    différents, tous doivent être lus et combinés en un seul résultat.
    """
    import json, zipfile

    (tmp_path / "a.json").write_text(
        json.dumps([{"username": "u1", "system": "AD", "account_status": "Active", "employee_status": "Active"}]),
        encoding="utf-8",
    )
    (tmp_path / "b.xml").write_text(
        "<accounts><account><username>u2</username><system>SAP</system>"
        "<account_status>Active</account_status><employee_status>Terminated</employee_status>"
        "</account></accounts>",
        encoding="utf-8",
    )

    zip_path = tmp_path / "export.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.write(tmp_path / "a.json", "a.json")
        zf.write(tmp_path / "b.xml", "b.xml")

    df = load_file(zip_path)
    assert len(df) == 2
    assert set(df["username"]) == {"u1", "u2"}
    print("OK - test_zip_with_multiple_formats")


def test_zip_ignores_unreadable_file_but_keeps_valid_ones(tmp_path):
    import json, zipfile

    (tmp_path / "good.json").write_text(
        json.dumps([{"username": "u1", "system": "AD", "account_status": "Active", "employee_status": "Active"}]),
        encoding="utf-8",
    )
    (tmp_path / "bad.txt").write_text(
        "Texte libre sans aucune structure reconnaissable pour un test.", encoding="utf-8"
    )

    zip_path = tmp_path / "export_partiel.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.write(tmp_path / "good.json", "good.json")
        zf.write(tmp_path / "bad.txt", "bad.txt")

    df = load_file(zip_path)  # ne doit PAS lever d'exception
    assert len(df) == 1
    print("OK - test_zip_ignores_unreadable_file_but_keeps_valid_ones")


def test_zip_with_no_valid_files_raises_error(tmp_path):
    import zipfile

    (tmp_path / "bad.txt").write_text("Texte totalement libre.", encoding="utf-8")
    zip_path = tmp_path / "export_vide.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.write(tmp_path / "bad.txt", "bad.txt")

    try:
        load_file(zip_path)
        assert False, "Une IngestionError aurait dû être levée"
    except IngestionError:
        print("OK - test_zip_with_no_valid_files_raises_error")


if __name__ == "__main__":
    import tempfile
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        test_json_list_of_records(tmp_path)
        test_json_wrapped_in_key(tmp_path)
        test_xml_repeated_elements(tmp_path)
        test_html_table(tmp_path)
        test_ldif_with_useraccountcontrol_decoding(tmp_path)
        test_pdf_table(tmp_path)
        test_zip_with_multiple_formats(tmp_path)
        test_zip_ignores_unreadable_file_but_keeps_valid_ones(tmp_path)
        test_zip_with_no_valid_files_raises_error(tmp_path)
    print("\nTous les tests sont passés.")


def test_multi_sheet_excel_reads_all_sheets():
    """
    Un classeur Excel avec plusieurs feuilles (une par système, cas
    fréquent) ne doit pas se limiter à la première feuille — chaque
    feuille doit être lue, avec son nom utilisé comme système par défaut.
    """
    import tempfile
    import pandas as pd
    from ingestion.ingest import load_file

    with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
        tmp_path = tmp.name
    with pd.ExcelWriter(tmp_path) as writer:
        pd.DataFrame({"SAM Account Name": ["user1", "user2"], "Account Status": ["Active", "Active"]}).to_excel(
            writer, sheet_name="Active Directory", index=False
        )
        pd.DataFrame({"SAM Account Name": ["user3"], "Account Status": ["Active"]}).to_excel(
            writer, sheet_name="CRM", index=False
        )

    df = load_file(tmp_path)
    assert len(df) == 3
    assert set(df["system"]) == {"Active Directory", "CRM"}
    print("OK - test_multi_sheet_excel_reads_all_sheets")


def test_multi_sheet_excel_respects_explicit_default_system():
    """Un default_system explicite doit primer sur les noms de feuilles."""
    import tempfile
    import pandas as pd
    from ingestion.ingest import load_file

    with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
        tmp_path = tmp.name
    with pd.ExcelWriter(tmp_path) as writer:
        pd.DataFrame({"SAM Account Name": ["user1"], "Account Status": ["Active"]}).to_excel(
            writer, sheet_name="Feuille1", index=False
        )
        pd.DataFrame({"SAM Account Name": ["user2"], "Account Status": ["Active"]}).to_excel(
            writer, sheet_name="Feuille2", index=False
        )

    df = load_file(tmp_path, default_system="Forcé")
    assert set(df["system"]) == {"Forcé"}
    print("OK - test_multi_sheet_excel_respects_explicit_default_system")


def test_single_sheet_excel_uses_filename_not_sheet_name():
    """
    Un classeur à une seule feuille doit utiliser le nom du fichier comme
    système par défaut (plus parlant), pas le nom générique de la feuille
    ('Sheet1') — la logique par nom de feuille ne s'applique qu'à partir
    de 2 feuilles, là où elle sert vraiment à les distinguer.
    """
    import tempfile
    import pandas as pd
    from ingestion.ingest import load_file

    with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False, prefix="ActiveDirectory_export_") as tmp:
        tmp_path = tmp.name
    pd.DataFrame({"SAM Account Name": ["user1"], "Account Status": ["Active"]}).to_excel(tmp_path, index=False)

    df = load_file(tmp_path)
    assert df.loc[0, "system"] != "Sheet1"
    assert "ActiveDirectory" in df.loc[0, "system"]
    print("OK - test_single_sheet_excel_uses_filename_not_sheet_name")


def test_column_split_across_sheets_merged_not_stacked():
    """
    Deux feuilles décrivant les MÊMES comptes avec des colonnes
    différentes (ex. 'Identités' avec noms/connexions, 'Rôles' avec les
    habilitations) doivent être fusionnées par colonne (jointure sur
    username) — un compte = une ligne complète — plutôt qu'empilées en
    deux lignes à moitié vides chacune.
    """
    import tempfile
    import pandas as pd
    from ingestion.ingest import load_file

    with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
        tmp_path = tmp.name
    with pd.ExcelWriter(tmp_path) as writer:
        pd.DataFrame({
            "SAM Account Name": ["user1", "user2"],
            "Display Name": ["Jean Dupont", "Konan Brou"],
        }).to_excel(writer, sheet_name="Identités", index=False)
        pd.DataFrame({
            "SAM Account Name": ["user1", "user2"],
            "Assigned User Roles": ["Admin", "User"],
        }).to_excel(writer, sheet_name="Rôles", index=False)

    df = load_file(tmp_path, default_system="Test")
    assert len(df) == 2, "Les comptes ne doivent pas être dupliqués/éclatés"
    row = df[df["username"] == "user1"].iloc[0]
    assert row["full_name"] == "Jean Dupont"
    assert row["role"] == "Admin"
    print("OK - test_column_split_across_sheets_merged_not_stacked")


def test_partial_overlap_below_threshold_stays_stacked():
    """
    Un recouvrement de comptes faible entre deux feuilles (sous le seuil)
    doit rester empilé, pas fusionné à tort — évite de fusionner deux
    systèmes différents qui partagent juste un compte administrateur
    commun par coïncidence.
    """
    import tempfile
    import pandas as pd
    from ingestion.ingest import load_file

    with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
        tmp_path = tmp.name
    with pd.ExcelWriter(tmp_path) as writer:
        pd.DataFrame({
            "SAM Account Name": ["shared_admin", "user_a", "user_b", "user_c"],
            "Account Status": ["Active"] * 4,
        }).to_excel(writer, sheet_name="SystemeA", index=False)
        pd.DataFrame({
            "SAM Account Name": ["shared_admin", "user_x", "user_y", "user_z"],
            "Account Status": ["Active"] * 4,
        }).to_excel(writer, sheet_name="SystemeB", index=False)

    df = load_file(tmp_path)
    assert len(df) == 8, "Recouvrement de 25% : doit rester empilé, pas fusionné"
    print("OK - test_partial_overlap_below_threshold_stays_stacked")


def test_zip_column_split_merged_not_stacked():
    """Un ZIP avec deux fichiers décrivant les mêmes comptes (colonnes
    différentes) doit fusionner par colonne, comme pour Excel/Word."""
    import zipfile
    import tempfile
    from ingestion.ingest import load_file

    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
        tmp_path = tmp.name
    with zipfile.ZipFile(tmp_path, "w") as zf:
        zf.writestr("identites.csv", "SAM Account Name,Display Name\nuser1,Jean Dupont\nuser2,Konan Brou\n")
        zf.writestr("roles.csv", "SAM Account Name,Assigned User Roles\nuser1,Admin\nuser2,User\n")

    df = load_file(tmp_path, default_system="Test")
    assert len(df) == 2
    row = df[df["username"] == "user1"].iloc[0]
    assert row["full_name"] == "Jean Dupont"
    assert row["role"] == "Admin"
    print("OK - test_zip_column_split_merged_not_stacked")


def test_docx_multiple_tables_different_column_counts_not_lost():
    """
    Avant correction, deux tableaux Word à nombre de colonnes différent
    faisaient perdre silencieusement le second tableau en entier. Ce test
    verrouille la correction.
    """
    import tempfile
    from docx import Document
    from ingestion.ingest import load_file

    doc = Document()
    t1 = doc.add_table(rows=2, cols=2)
    t1.cell(0, 0).text = "SAM Account Name"; t1.cell(0, 1).text = "Display Name"
    t1.cell(1, 0).text = "user1"; t1.cell(1, 1).text = "Jean Dupont"
    t2 = doc.add_table(rows=2, cols=3)
    t2.cell(0, 0).text = "SAM Account Name"; t2.cell(0, 1).text = "Department"; t2.cell(0, 2).text = "Assigned User Roles"
    t2.cell(1, 0).text = "user1"; t2.cell(1, 1).text = "IT"; t2.cell(1, 2).text = "Admin"

    with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as tmp:
        tmp_path = tmp.name
    doc.save(tmp_path)

    df = load_file(tmp_path, default_system="Test")
    assert len(df) == 1
    assert df.loc[0, "full_name"] == "Jean Dupont"
    assert df.loc[0, "role"] == "Admin"
    assert df.loc[0, "department"] == "IT"
    print("OK - test_docx_multiple_tables_different_column_counts_not_lost")


def test_pdf_column_split_tables_merged_not_stacked():
    """
    Deux tableaux PDF décrivant les mêmes comptes avec des colonnes
    différentes (même nombre de colonnes par coïncidence) doivent être
    fusionnés par colonne, pas empilés à tort — avant correction, un
    simple comptage de colonnes confondait les deux tableaux.
    """
    import tempfile
    from reportlab.lib.pagesizes import A4
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, PageBreak
    from reportlab.lib import colors
    from ingestion.ingest import load_file

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp_path = tmp.name
    doc = SimpleDocTemplate(tmp_path, pagesize=A4)
    t1 = Table([["SAM Account Name", "Display Name"], ["user1", "Jean Dupont"], ["user2", "Konan Brou"]])
    t1.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.5, colors.black)]))
    t2 = Table([["SAM Account Name", "Assigned User Roles"], ["user1", "Admin"], ["user2", "User"]])
    t2.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.5, colors.black)]))
    doc.build([t1, PageBreak(), t2])

    df = load_file(tmp_path, default_system="Test")
    assert len(df) == 2
    row = df[df["username"] == "user1"].iloc[0]
    assert row["full_name"] == "Jean Dupont"
    assert row["role"] == "Admin"
    print("OK - test_pdf_column_split_tables_merged_not_stacked")


def test_pdf_header_repeated_mid_page_not_counted_as_data():
    """
    Un en-tête qui réapparaît au milieu d'une page (pas seulement en haut
    de chaque nouvelle page — cas réel rencontré quand le PDF source
    assemble plusieurs petits tableaux qui ne s'alignent pas avec les
    sauts de page) ne doit jamais être compté comme une ligne de données.
    """
    import tempfile
    from reportlab.lib.pagesizes import A4
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle
    from reportlab.lib import colors
    from ingestion.ingest import load_file

    header = ["SAM Account Name", "Display Name", "Account Status"]
    rows_data = [header] + [[f"user{i:03d}", f"Utilisateur Test {i}", "Active"] for i in range(1, 21)]

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp_path = tmp.name
    doc = SimpleDocTemplate(tmp_path, pagesize=A4)
    # Deux tableaux consécutifs SANS saut de page entre eux, chacun avec
    # son propre en-tête répété — simule un en-tête réapparaissant au
    # milieu d'une même page.
    t1 = Table(rows_data[:11])
    t1.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.5, colors.black)]))
    t2 = Table([header] + rows_data[11:])
    t2.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.5, colors.black)]))
    doc.build([t1, t2])

    df = load_file(tmp_path)
    assert len(df) == 20, f"Attendu 20 lignes, obtenu {len(df)}"
    assert df["username"].duplicated().sum() == 0
    assert "SAM Account Name" not in df["username"].values
    print("OK - test_pdf_header_repeated_mid_page_not_counted_as_data")


def test_image_ocr_extraction_and_warning_flag():
    """
    Une image contenant un tableau délimité doit être lisible par OCR,
    avec chaque ligne marquée '_ocr_source' pour signaler une fiabilité
    moindre qu'un fichier structuré.
    """
    import tempfile
    from PIL import Image, ImageDraw, ImageFont
    from ingestion.ingest import load_file

    img = Image.new("RGB", (900, 200), "white")
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", 28)
    except Exception:
        font = ImageFont.load_default()
    lines = ["SAM Account Name,Display Name,Account Status", "user2,Konan Brou,Active"]
    y = 30
    for line in lines:
        draw.text((30, y), line, fill="black", font=font)
        y += 50

    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        tmp_path = tmp.name
    img.save(tmp_path)

    df = load_file(tmp_path, default_system="Test")
    assert "_ocr_source" in df.columns
    assert df["_ocr_source"].all()
    print("OK - test_image_ocr_extraction_and_warning_flag")


def test_pdf_report_shows_ocr_warning_when_flagged():
    """Le rapport PDF doit afficher un avertissement visible si des
    données proviennent d'une reconnaissance OCR."""
    import pandas as pd
    from analysis.access_review import analyze_access
    from reporting.export import generate_pdf_report
    import pdfplumber

    df = pd.DataFrame({
        "username": ["user1"], "system": ["Test"], "_ocr_source": [True],
    })
    result = analyze_access(df)
    output = generate_pdf_report(result, "output/test_ocr_flag.pdf")
    with pdfplumber.open(output) as pdf:
        full_text = "\n".join(page.extract_text() or "" for page in pdf.pages)
    assert "WARNING" in full_text
    assert "OCR" in full_text
    print("OK - test_pdf_report_shows_ocr_warning_when_flagged")


def test_pdf_report_no_ocr_warning_without_flag():
    """Sans donnée OCR, aucun avertissement ne doit apparaître."""
    import pandas as pd
    from analysis.access_review import analyze_access
    from reporting.export import generate_pdf_report
    import pdfplumber

    df = pd.DataFrame({"username": ["user1"], "system": ["Test"]})
    result = analyze_access(df)
    output = generate_pdf_report(result, "output/test_no_ocr_flag.pdf")
    with pdfplumber.open(output) as pdf:
        full_text = "\n".join(page.extract_text() or "" for page in pdf.pages)
    assert "AVERTISSEMENT" not in full_text
    print("OK - test_pdf_report_no_ocr_warning_without_flag")


def test_utf16_encoding_with_bom_detected_correctly():
    """
    Vrai bug trouvé par balayage systématique : un fichier UTF-16 (avec
    ou sans BOM) était silencieusement lu comme cp1252/latin-1 (des
    encodages mono-octet qui n'échouent presque jamais), produisant du
    texte truffé d'octets nuls sans la moindre erreur — jamais détecté
    comme UTF-16.
    """
    import tempfile
    from pathlib import Path
    from ingestion.ingest import _detect_encoding

    content = "username,system\nu1,AD\n".encode("utf-16")
    with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as tmp:
        tmp.write(content)
        path = Path(tmp.name)
    encoding = _detect_encoding(path)
    with open(path, encoding=encoding) as f:
        text = f.read()
    assert "\x00" not in text
    assert "username" in text
    print("OK - test_utf16_encoding_with_bom_detected_correctly")


def test_utf16_encoding_without_bom_detected_correctly():
    """
    Cas encore plus piégeux : sans BOM, un octet NUL est du UTF-8 VALIDE
    (c'est le caractère NUL) — raw.decode("utf-8") réussit donc
    trivialement sur un fichier UTF-16, avant même d'atteindre la
    détection dédiée. Testé pour les deux ordres d'octets (LE et BE),
    la position des octets nuls devant déterminer lequel, pas un essai
    à l'aveugle.
    """
    import tempfile
    from pathlib import Path
    from ingestion.ingest import _detect_encoding

    for byte_order in ("utf-16-le", "utf-16-be"):
        content = "username,system\nu1,AD\n".encode(byte_order)
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as tmp:
            tmp.write(content)
            path = Path(tmp.name)
        encoding = _detect_encoding(path)
        with open(path, encoding=encoding) as f:
            text = f.read()
        assert "\x00" not in text, f"Corrompu pour {byte_order}"
        assert "username" in text, f"Corrompu pour {byte_order}"
    print("OK - test_utf16_encoding_without_bom_detected_correctly")


def test_utf16_full_pipeline_no_corruption():
    """Le pipeline d'ingestion complet doit produire des colonnes et
    valeurs propres pour un fichier UTF-16, pas des noms de colonnes
    truffés d'octets nuls invisibles."""
    import tempfile
    from ingestion.ingest import load_file

    content = "username,system\nu1,AD\n".encode("utf-16-be")
    with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as tmp:
        tmp.write(content)
        path = tmp.name
    df = load_file(path, default_system="Test")
    assert list(df.columns) == ["username", "system"]
    assert df.loc[0, "username"] == "u1"
    print("OK - test_utf16_full_pipeline_no_corruption")


def test_user_name_two_words_variant_recognized():
    """
    Vrai gap trouvé : 'User Name' (avec espace, très courant dans les
    exports Windows/IAM génériques) n'était pas reconnu comme variante
    de 'username' — seules les formes avec 'logon'/'sam account' étaient
    couvertes, la forme la plus simple et la plus fréquente manquait.
    """
    import tempfile
    from ingestion.ingest import load_file

    content = "USER NAME , SYSTEM , ACCOUNT STATUS\nu1,AD,Active\n"
    with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as tmp:
        tmp.write(content)
        path = tmp.name
    df = load_file(path, default_system="Test")
    assert "username" in df.columns
    assert df.loc[0, "username"] == "u1"
    print("OK - test_user_name_two_words_variant_recognized")


def test_pipe_delimiter_recognized_in_csv_files():
    """
    Vrai gap trouvé, avec incohérence entre formats : le pipe (|) était
    déjà reconnu comme séparateur pour les fichiers .txt (_try_delimited)
    mais pas pour les fichiers .csv (_read_ragged_csv) — le même contenu
    aurait donc été traité différemment selon la seule extension du
    fichier, sans raison de fond.
    """
    import tempfile
    from ingestion.ingest import load_file

    content = b"username|system|account_status\nu1|AD|Active\n"
    with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as tmp:
        tmp.write(content)
        path = tmp.name
    df = load_file(path, default_system="Test")
    assert list(df.columns) == ["username", "system", "account_status"]
    assert df.loc[0, "username"] == "u1"
    print("OK - test_pipe_delimiter_recognized_in_csv_files")


def test_multi_sheet_merge_conflict_detected_and_warned():
    """
    Vrai bug trouvé, le plus sérieux de cette session : quand deux
    feuilles Excel décrivent le MÊME compte avec des valeurs
    DIFFÉRENTES pour le même champ (ex. 'Active' dans l'une, 'Disabled'
    dans l'autre), la fusion (combine_first) gardait silencieusement une
    valeur et perdait l'autre — sans la moindre trace. Pour un outil
    d'audit IAM, ça pouvait faire passer un compte réellement désactivé
    pour actif. Un avertissement explicite doit maintenant signaler
    tout conflit réel, même si une valeur doit toujours être choisie
    pour continuer.
    """
    import tempfile, os, logging
    import pandas as pd
    from ingestion.ingest import load_file

    with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
        path = tmp.name
    with pd.ExcelWriter(path) as writer:
        pd.DataFrame({"username": ["u1"], "system": ["AD"], "account_status": ["Active"]}).to_excel(
            writer, sheet_name="Feuille1", index=False)
        pd.DataFrame({"username": ["u1"], "system": ["AD"], "account_status": ["Disabled"]}).to_excel(
            writer, sheet_name="Feuille2", index=False)

    caplog_records = []
    logger = logging.getLogger("ingestion")
    handler = logging.Handler()
    handler.emit = lambda record: caplog_records.append(record)
    logger.addHandler(handler)
    try:
        df = load_file(path, default_system="Test")
    finally:
        logger.removeHandler(handler)
        os.unlink(path)

    assert len(df) == 1  # une seule ligne malgré le conflit, une valeur a dû être choisie
    warnings_text = " ".join(r.getMessage() for r in caplog_records if r.levelno >= logging.WARNING)
    assert "Conflit de données" in warnings_text or "conflit" in warnings_text.lower()
    print("OK - test_multi_sheet_merge_conflict_detected_and_warned")


def test_multi_sheet_merge_no_false_positive_warning():
    """Une fusion propre (colonnes complémentaires, pas de conflit réel)
    ne doit déclencher aucun avertissement de conflit."""
    import tempfile, os, logging
    import pandas as pd
    from ingestion.ingest import load_file

    with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
        path = tmp.name
    with pd.ExcelWriter(path) as writer:
        pd.DataFrame({"username": ["u1"], "system": ["AD"], "account_status": ["Active"]}).to_excel(
            writer, sheet_name="Identites", index=False)
        pd.DataFrame({"username": ["u1"], "manager": ["Alice"]}).to_excel(
            writer, sheet_name="Managers", index=False)

    caplog_records = []
    logger = logging.getLogger("ingestion")
    handler = logging.Handler()
    handler.emit = lambda record: caplog_records.append(record)
    logger.addHandler(handler)
    try:
        df = load_file(path, default_system="Test")
    finally:
        logger.removeHandler(handler)
        os.unlink(path)

    warnings_text = " ".join(r.getMessage() for r in caplog_records if r.levelno >= logging.WARNING)
    assert "Conflit de données" not in warnings_text
    assert df.loc[0, "manager"] == "Alice"
    print("OK - test_multi_sheet_merge_no_false_positive_warning")


def test_generic_accounts_across_different_systems_not_falsely_merged():
    """
    Vrai bug sérieux trouvé, exposé par le correctif précédent sur les
    conflits de fusion : deux systèmes RÉELLEMENT différents (AD et SAP)
    se faisaient fusionner par colonne à tort (perdant silencieusement
    des comptes) simplement parce qu'ils partagent des noms de comptes
    génériques ('admin', 'test' — présents indépendamment sur de
    nombreux systèmes en pratique, pas la même personne). Si les deux
    tables précisent déjà un système EXPLICITE et que ces systèmes sont
    clairement différents, c'est empilé plutôt que fusionné, même avec
    un fort recouvrement de noms.
    """
    import tempfile, os
    import pandas as pd
    from ingestion.ingest import load_file

    with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
        path = tmp.name
    with pd.ExcelWriter(path) as writer:
        pd.DataFrame({
            "username": ["admin", "jdupont", "mmartin", "test"], "system": ["AD"] * 4,
            "account_status": ["Active"] * 4,
        }).to_excel(writer, sheet_name="SystemeA", index=False)
        pd.DataFrame({
            "username": ["admin", "test", "kbrou", "asylla"], "system": ["SAP"] * 4,
            "account_status": ["Active"] * 4,
        }).to_excel(writer, sheet_name="SystemeB", index=False)

    df = load_file(path, default_system="Test")
    os.unlink(path)

    assert len(df) == 8, f"Attendu 8 comptes distincts, obtenu {len(df)} — perte de données silencieuse"
    admin_rows = df[df["username"] == "admin"]
    assert len(admin_rows) == 2
    assert set(admin_rows["system"]) == {"AD", "SAP"}
    print("OK - test_generic_accounts_across_different_systems_not_falsely_merged")


def test_legitimate_merge_still_works_with_explicit_same_system():
    """La correction ci-dessus ne doit pas casser une fusion légitime :
    deux feuilles décrivant le même système explicite doivent toujours
    fusionner par colonne comme avant."""
    import tempfile, os
    import pandas as pd
    from ingestion.ingest import load_file

    with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
        path = tmp.name
    with pd.ExcelWriter(path) as writer:
        pd.DataFrame({"username": ["u1", "u2"], "system": ["AD", "AD"], "account_status": ["Active", "Active"]}).to_excel(
            writer, sheet_name="Feuille1", index=False)
        pd.DataFrame({"username": ["u1", "u2"], "system": ["AD", "AD"], "manager": ["Alice", "Bob"]}).to_excel(
            writer, sheet_name="Feuille2", index=False)

    df = load_file(path, default_system="Test")
    os.unlink(path)

    assert len(df) == 2
    assert "manager" in df.columns
    assert df.loc[df["username"] == "u1", "manager"].iloc[0] == "Alice"
    print("OK - test_legitimate_merge_still_works_with_explicit_same_system")


def test_generic_accounts_not_falsely_merged_when_sheet_name_is_system():
    """
    Extension du bug précédent, trouvée en creusant plus loin : le
    premier correctif ne couvrait que le cas d'une colonne 'system'
    déjà explicite. Si c'est le NOM DE LA FEUILLE qui sert de système
    (très courant, aucune colonne 'system' dans les données), le premier
    correctif ne s'appliquait pas du tout et le bug restait entier — en
    plus, les identités des systèmes ('AD', 'SAP') étaient totalement
    perdues (remplacées par le nom du fichier temporaire). Corrigé à la
    racine : les comptes génériques sont exclus du calcul de
    recouvrement qui décide fusion/empilement, peu importe d'où vient
    finalement la valeur 'system'.
    """
    import tempfile, os
    import pandas as pd
    from ingestion.ingest import load_file

    with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
        path = tmp.name
    with pd.ExcelWriter(path) as writer:
        pd.DataFrame({
            "username": ["admin", "jdupont", "mmartin", "test"], "account_status": ["Active"] * 4,
        }).to_excel(writer, sheet_name="AD", index=False)
        pd.DataFrame({
            "username": ["admin", "test", "kbrou", "asylla"], "account_status": ["Active"] * 4,
        }).to_excel(writer, sheet_name="SAP", index=False)

    df = load_file(path)
    os.unlink(path)

    assert len(df) == 8
    assert set(df["system"].unique()) == {"AD", "SAP"}
    admin_rows = df[df["username"] == "admin"]
    assert set(admin_rows["system"]) == {"AD", "SAP"}
    print("OK - test_generic_accounts_not_falsely_merged_when_sheet_name_is_system")


def test_generic_accounts_fix_applies_to_word_documents_too():
    """La même correction doit s'appliquer à Word (plusieurs tableaux
    dans un document), puisque tous les formats passent par la même
    fonction de décision fusion/empilement."""
    import tempfile, os
    from docx import Document
    from ingestion.ingest import load_file

    doc = Document()
    table1 = doc.add_table(rows=5, cols=2)
    table1.cell(0, 0).text = "username"; table1.cell(0, 1).text = "account_status"
    for i, (u, s) in enumerate([("admin", "Active"), ("jdupont", "Active"), ("mmartin", "Active"), ("test", "Active")], start=1):
        table1.cell(i, 0).text = u; table1.cell(i, 1).text = s
    table2 = doc.add_table(rows=5, cols=2)
    table2.cell(0, 0).text = "username"; table2.cell(0, 1).text = "account_status"
    for i, (u, s) in enumerate([("admin", "Active"), ("test", "Active"), ("kbrou", "Active"), ("asylla", "Active")], start=1):
        table2.cell(i, 0).text = u; table2.cell(i, 1).text = s

    with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as tmp:
        path = tmp.name
    doc.save(path)

    df = load_file(path, default_system="Test")
    os.unlink(path)

    assert len(df) == 8
    print("OK - test_generic_accounts_fix_applies_to_word_documents_too")


def test_generic_accounts_fix_applies_to_zip_archives_too():
    """La même correction doit s'appliquer à une archive ZIP contenant
    plusieurs fichiers, un système par fichier nommé."""
    import tempfile, os, zipfile
    from ingestion.ingest import load_file

    csv1 = "username,account_status\nadmin,Active\njdupont,Active\nmmartin,Active\ntest,Active\n"
    csv2 = "username,account_status\nadmin,Active\ntest,Active\nkbrou,Active\nasylla,Active\n"
    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
        zip_path = tmp.name
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("AD.csv", csv1)
        zf.writestr("SAP.csv", csv2)

    df = load_file(zip_path)
    os.unlink(zip_path)

    assert len(df) == 8
    assert set(df["system"].unique()) == {"AD", "SAP"}
    print("OK - test_generic_accounts_fix_applies_to_zip_archives_too")


def test_duplicate_raw_columns_with_real_conflict_warns():
    """
    Vrai bug trouvé en poussant la fiabilité au maximum : deux colonnes
    brutes portant le même libellé exact (ex. deux colonnes 'role' dans
    un export mal formé) avec des valeurs RÉELLEMENT différentes sur
    une même ligne étaient fusionnées via combine_first() sans jamais
    signaler ce désaccord — même risque que la fusion multi-feuilles
    (déjà corrigé ailleurs), jamais appliqué à ce cas de colonnes en
    double au sein d'un même tableau.
    """
    import tempfile
    from ingestion.ingest import load_file

    content = "username,system,role,role\nu1,AD,Admin,Standard\n"
    with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as tmp:
        tmp.write(content)
        path = tmp.name
    import logging
    import io
    log_stream = io.StringIO()
    handler = logging.StreamHandler(log_stream)
    logging.getLogger("ingestion").addHandler(handler)
    df = load_file(path, default_system="Test")
    logging.getLogger("ingestion").removeHandler(handler)
    assert "valeurs différentes" in log_stream.getvalue()
    assert df.loc[0, "role"] == "Admin"
    print("OK - test_duplicate_raw_columns_with_real_conflict_warns")


def test_duplicate_raw_columns_complementary_no_false_positive():
    """Deux colonnes en double dont les valeurs sont complémentaires
    (l'une vide, l'autre remplie, jamais les deux à la fois sur une même
    ligne) ne doivent PAS déclencher l'avertissement de conflit — une
    chaîne vide ne doit pas être traitée comme une valeur réelle en
    désaccord."""
    import tempfile
    from ingestion.ingest import load_file
    import logging
    import io

    content = "username,system,role,role\nu1,AD,Admin,\nu2,AD,,Standard\n"
    with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as tmp:
        tmp.write(content)
        path = tmp.name
    log_stream = io.StringIO()
    handler = logging.StreamHandler(log_stream)
    logging.getLogger("ingestion").addHandler(handler)
    df = load_file(path, default_system="Test")
    logging.getLogger("ingestion").removeHandler(handler)
    assert "valeurs différentes" not in log_stream.getvalue()
    assert df.loc[0, "role"] == "Admin"
    assert df.loc[1, "role"] == "Standard"
    print("OK - test_duplicate_raw_columns_complementary_no_false_positive")


def test_excel_formula_error_cells_trigger_warning():
    """
    Vrai bug trouvé en poussant la fiabilité au maximum : openpyxl
    reconnaît automatiquement les cellules d'erreur de formule Excel
    ('#REF!', '#DIV/0!', '#N/A'...) comme un type de donnée distinct, et
    pandas les convertit silencieusement en valeur manquante —
    indiscernable d'une case réellement vide. Une formule cassée dans le
    fichier source est pourtant un signal différent (problème dans le
    fichier lui-même), qui mérite un avertissement plutôt que de
    disparaître sans trace.
    """
    import openpyxl
    import tempfile
    import logging
    import io
    import pandas as pd
    from ingestion.ingest import load_file

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["username", "system", "account_status"])
    ws.append(["u1", "AD", "Active"])
    ws.append(["u2", "AD", "#REF!"])
    with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
        path = tmp.name
    wb.save(path)

    log_stream = io.StringIO()
    handler = logging.StreamHandler(log_stream)
    logging.getLogger("ingestion").addHandler(handler)
    df = load_file(path, default_system="Test")
    logging.getLogger("ingestion").removeHandler(handler)

    assert "erreur de formule Excel" in log_stream.getvalue()
    assert pd.isna(df.loc[1, "account_status"])
    print("OK - test_excel_formula_error_cells_trigger_warning")


def test_excel_without_formula_errors_no_false_positive_warning():
    """Un fichier Excel normal, sans cellule d'erreur, ne doit jamais
    déclencher cet avertissement."""
    import openpyxl
    import tempfile
    import logging
    import io
    from ingestion.ingest import load_file

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["username", "system", "account_status"])
    ws.append(["u1", "AD", "Active"])
    with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
        path = tmp.name
    wb.save(path)

    log_stream = io.StringIO()
    handler = logging.StreamHandler(log_stream)
    logging.getLogger("ingestion").addHandler(handler)
    load_file(path, default_system="Test")
    logging.getLogger("ingestion").removeHandler(handler)

    assert "erreur de formule Excel" not in log_stream.getvalue()
    print("OK - test_excel_without_formula_errors_no_false_positive_warning")


def test_future_login_date_flagged_as_data_quality_issue():
    """
    Trouvé en poussant la fiabilité au maximum : une date de dernière
    connexion dans le FUTUR (par rapport à la date d'extraction) est
    structurellement impossible — toujours une erreur (saisie manuelle,
    décalage d'horloge...), jamais une simple ambiguïté de format comme
    pour les dates déjà détectées. Distinct des 'invalid_dates' :
    ici la date EST interprétée, mais le résultat ne peut pas être vrai.
    """
    import pandas as pd
    from ingestion.ingest import compute_data_quality_report

    df = pd.DataFrame({
        "username": ["u1", "u2"], "system": ["AD"] * 2,
        "last_login_date": ["2027-01-01", "2026-01-01"],
    })
    report = compute_data_quality_report(df)
    assert report["issues"]["future_dates"] == 1
    print("OK - test_future_login_date_flagged_as_data_quality_issue")


def test_merged_cells_forward_fill_manager_column():
    """
    Vrai bug trouvé en poussant la fiabilité au maximum : Excel n'écrit
    la valeur d'une cellule fusionnée que dans la cellule en haut à
    gauche de la plage — les autres restent vides en interne, même si
    elles affichent visuellement la même valeur dans le tableur (motif
    très courant : fusionner 'manager' ou 'système' sur plusieurs lignes
    pour éviter la répétition visuelle). Sans traitement, ces comptes
    étaient signalés à tort 'sans manager identifié' alors que le
    manager est clairement affiché pour chacun d'eux dans le fichier.
    """
    import openpyxl
    import tempfile
    from ingestion.ingest import load_file
    from analysis.access_review import analyze_access

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["username", "system", "manager", "account_status"])
    ws.append(["u1", "AD", "Marie Diallo", "Active"])
    ws.append(["u2", "AD", None, "Active"])
    ws.append(["u3", "AD", None, "Active"])
    ws.merge_cells("C2:C4")
    with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
        path = tmp.name
    wb.save(path)

    df = load_file(path, default_system="Test")
    assert df["manager"].tolist() == ["Marie Diallo", "Marie Diallo", "Marie Diallo"]
    result = analyze_access(df)
    assert result["has_no_manager"].tolist() == [False, False, False]
    print("OK - test_merged_cells_forward_fill_manager_column")


def test_merged_title_cell_does_not_break_ingestion():
    """Une cellule fusionnée utilisée comme titre (hors zone de données)
    ne doit pas perturber l'ingestion normale — non-régression."""
    import openpyxl
    import tempfile
    from ingestion.ingest import load_file

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.merge_cells("A1:D1")
    ws["A1"] = "Export mensuel"
    ws.append(["username", "system", "account_status", "last_login_date"])
    ws.append(["u1", "AD", "Active", "2026-01-01"])
    with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
        path = tmp.name
    wb.save(path)

    df = load_file(path, default_system="Test")
    assert df.loc[0, "username"] == "u1"
    assert df.loc[0, "account_status"] == "Active"
    print("OK - test_merged_title_cell_does_not_break_ingestion")


def test_data_quality_duplicate_username_detection_is_case_insensitive():
    """
    Vrai bug trouvé en poussant la fiabilité au maximum, cohérent avec
    le motif de casse déjà corrigé partout ailleurs cette session : le
    même compte apparaissant deux fois avec une casse différente
    ('jdupont' puis 'JDupont' — variation d'export réaliste) n'était pas
    détecté comme doublon par le contrôle qualité, une comparaison
    stricte ratant ce cas pourtant emblématique.
    """
    import pandas as pd
    from ingestion.ingest import compute_data_quality_report

    df = pd.DataFrame({
        "username": ["jdupont", "JDupont"], "full_name": ["Jean Dupont"] * 2,
        "system": ["AD"] * 2, "account_status": ["Active"] * 2,
    })
    report = compute_data_quality_report(df)
    assert report["issues"]["duplicate_usernames"] == 2

    different_accounts = pd.DataFrame({
        "username": ["jdupont", "mmartin"], "system": ["AD"] * 2, "account_status": ["Active"] * 2,
    })
    report2 = compute_data_quality_report(different_accounts)
    assert report2["issues"]["duplicate_usernames"] == 0
    print("OK - test_data_quality_duplicate_username_detection_is_case_insensitive")


def test_wso2_style_identity_export_columns_recognized():
    """
    Vrai fichier réel (export IAM type WSO2 Identity Server, avec des
    colonnes préfixées 'identity/...') fourni par l'utilisateur pour
    vérification. Deux trouvailles critiques :

    1. Le caractère '/' n'était pas traité comme séparateur dans la
       normalisation des noms de colonnes, faisant échouer TOUTE colonne
       de la forme 'identity/xxx' — notamment 'identity/lastLoginTime'
       et 'identity/lastPasswordUpdateTime', désactivant silencieusement
       la détection de dormance ET le contrôle d'âge des mots de passe.
    2. 'givenname' (LDAP : prénom seul) était listé par erreur comme
       variante de full_name plutôt que de first_name — un prénom seul
       ('Jean') pouvait ainsi écraser silencieusement un nom complet
       correct ('Jean Dupont') provenant d'une autre colonne.
    """
    import csv
    import tempfile
    from ingestion.ingest import load_file
    from analysis.access_review import analyze_access

    headers = [
        "id", "username", "emailaddress", "givenname", "lastname", "status", "created",
        "linemanageremail", "identity/accountState",
        "identity/lastLoginTime", "identity/lastPasswordUpdateTime",
        "mobile", "fullname", "roles",
    ]
    row = [
        "12345", "jdupont", "jean.dupont@mtn.ci", "Jean", "Dupont", "ACTIVE", "2024-01-15",
        "manager@mtn.ci", "ACTIVE",
        "2026-09-01T10:30:00Z", "2026-06-01T08:00:00Z",
        "+225 07 00 00 00", "Jean Dupont", "Standard User",
    ]
    with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, newline="") as tmp:
        writer = csv.writer(tmp)
        writer.writerow(headers)
        writer.writerow(row)
        path = tmp.name

    df = load_file(path, default_system="Test")
    assert df.loc[0, "full_name"] == "Jean Dupont"  # pas juste 'Jean' (bug givenname corrigé)
    assert df.loc[0, "last_login_date"] == "2026-09-01T10:30:00Z"
    assert df.loc[0, "password_last_set"] == "2026-06-01T08:00:00Z"
    assert df.loc[0, "email"] == "jean.dupont@mtn.ci"
    assert df.loc[0, "manager"] == "manager@mtn.ci"

    result = analyze_access(df)
    # La dormance et l'âge du mot de passe doivent être calculables
    # (pas None), preuve que les colonnes sont bien exploitées, pas
    # seulement présentes sous le bon nom.
    assert result.loc[0, "days_since_last_login"] is not None
    print("OK - test_wso2_style_identity_export_columns_recognized")


def test_slash_in_column_name_treated_as_separator():
    """Le caractère '/' doit être traité comme un séparateur dans la
    normalisation des noms de colonnes, au même titre que '_' et '-' —
    sans quoi toute colonne de la forme 'namespace/champ' (convention
    courante des exports IAM type WSO2) ne serait jamais reconnue."""
    from ingestion.ingest import _normalize
    assert _normalize("identity/lastLoginTime") == _normalize("identity lastLoginTime")


def test_custom_column_mapping_persists_across_files():
    """
    Fonctionnalité demandée explicitement : aucune liste de variantes ne
    peut prévoir à l'avance tous les noms de colonnes qu'un futur export
    utilisera. Une correction manuelle enregistrée une fois (via
    save_custom_column_mapping, ce que fait le dashboard) doit être
    reconnue automatiquement sur tout futur fichier portant EXACTEMENT
    le même nom de colonne — vérifié de bout en bout : enregistrement
    d'une correspondance, puis chargement d'un fichier DIFFÉRENT (autres
    données) qui doit la reconnaître sans aucune intervention.
    """
    import tempfile
    from pathlib import Path
    from ingestion.ingest import load_file
    from ingestion.custom_column_mappings import (
        save_custom_column_mapping, load_custom_column_mappings,
    )

    store_path = Path(tempfile.gettempdir()) / f"test_learned_mapping_{tempfile.mktemp()[-8:]}.json"
    if store_path.exists():
        store_path.unlink()

    # Première fois : colonne non reconnue, correction manuelle simulée.
    content1 = "username,system,custom_field_xyz\nu1,AD,2026-01-01\n"
    with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as tmp1:
        tmp1.write(content1)
        path1 = tmp1.name
    df1 = load_file(path1, default_system="Test")
    assert "custom_field_xyz" in df1.attrs.get("unmapped_columns", [])
    save_custom_column_mapping("custom_field_xyz", "last_login_date", store_path=store_path)

    # Deuxième fichier, données différentes, MÊME nom de colonne : doit
    # être reconnu automatiquement, sans qu'on repasse par une correction.
    content2 = "username,system,custom_field_xyz\nu2,SAP,2026-08-15\n"
    with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as tmp2:
        tmp2.write(content2)
        path2 = tmp2.name
    learned = load_custom_column_mappings(store_path=store_path)
    df2 = load_file(path2, default_system="Test", custom_mappings=learned)
    assert "last_login_date" in df2.columns
    assert df2.loc[0, "last_login_date"] == "2026-08-15"
    assert "custom_field_xyz" not in df2.attrs.get("unmapped_columns", [])

    store_path.unlink()
    print("OK - test_custom_column_mapping_persists_across_files")


def test_accounts_created_priority_and_boolean_polarity_inversion():
    """
    Deux directives explicites, testées ensemble :

    1. Priorité inversée pour 'Accounts created' : quand une revue
       précédente EST fournie, la comparaison exacte prime sur la
       fenêtre de 90 jours (plus fiable — un compte créé 91 jours plus
       tôt mais réellement nouveau depuis le dernier cycle ne serait
       jamais détecté par la seule fenêtre fixe).

    2. Inversion de polarité pour les indicateurs booléens (ex.
       'identity/accountDisabled' : true signifie précisément que le
       compte n'est PAS actif) — sans cette inversion, fusionner tel
       quel produirait un statut inversé et silencieusement faux
       ('true' étant déjà reconnu comme marqueur actif par ailleurs).
    """
    import tempfile
    from ingestion.ingest import load_file
    from analysis.access_review import analyze_access

    content = "username,system,identity/accountDisabled\nu1,AD,true\nu2,AD,false\n"
    with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as tmp:
        tmp.write(content)
        path = tmp.name

    custom = {"identity accountdisabled": "account_status__inverted_bool"}
    df = load_file(path, default_system="Test", custom_mappings=custom)
    assert df.loc[0, "account_status"] == "Disabled"
    assert df.loc[1, "account_status"] == "Active"

    result = analyze_access(df)
    assert result.loc[0, "account_status"] == "Disabled"
    print("OK - test_accounts_created_priority_and_boolean_polarity_inversion")


def test_boolean_inversion_leaves_unrecognized_values_unchanged():
    """Une valeur qui n'est ni un marqueur vrai/faux reconnu ('Unknown'
    par exemple) doit rester inchangée plutôt que d'être écrasée par
    une supposition."""
    import tempfile
    from ingestion.ingest import load_file

    content = "username,system,identity/accountDisabled\nu1,AD,Unknown\n"
    with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as tmp:
        tmp.write(content)
        path = tmp.name

    custom = {"identity accountdisabled": "account_status__inverted_bool"}
    df = load_file(path, default_system="Test", custom_mappings=custom)
    assert df.loc[0, "account_status"] == "Unknown"
    print("OK - test_boolean_inversion_leaves_unrecognized_values_unchanged")
