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
