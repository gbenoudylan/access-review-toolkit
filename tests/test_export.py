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
