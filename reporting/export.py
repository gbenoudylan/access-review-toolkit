"""
Module d'export du rapport de revue d'accès.

Génère deux livrables à partir du DataFrame analysé (sortie de
analysis/access_review.py) :

    - Un rapport Excel détaillé avec mise en forme conditionnelle par
      niveau de risque, prêt pour le suivi opérationnel.
    - Un rapport PDF de synthèse, adapté à une diffusion managériale ou
      une preuve d'audit pour la revue d'accès périodique.
"""

from __future__ import annotations
import logging
from datetime import datetime
from pathlib import Path

import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer

logger = logging.getLogger("export")

RISK_COLORS_HEX = {
    "Critique": "D62728",
    "Élevé": "FF7F0E",
    "Moyen": "FFD700",
    "Faible": "2CA02C",
}

DISPLAY_COLUMNS = [
    ("username", "Compte"),
    ("full_name", "Nom"),
    ("department", "Département"),
    ("system", "Système"),
    ("manager", "Manager"),
    ("account_status", "Statut compte"),
    ("employee_status", "Statut RH"),
    ("days_since_last_login", "Jours sans connexion"),
    ("is_privileged_flag", "Privilégié"),
    ("review_action", "Action recommandée"),
    ("risk_level", "Risque"),
]


def _prepare_export_df(df: pd.DataFrame) -> pd.DataFrame:
    available = [(col, label) for col, label in DISPLAY_COLUMNS if col in df.columns]
    export_df = df[[col for col, _ in available]].copy()
    export_df.columns = [label for _, label in available]

    risk_order = {"Critique": 0, "Élevé": 1, "Moyen": 2, "Faible": 3}
    if "Risque" in export_df.columns:
        export_df["_sort"] = export_df["Risque"].map(risk_order).fillna(99)
        export_df = export_df.sort_values("_sort").drop(columns="_sort")
    return export_df


# ---------------------------------------------------------------------
# EXCEL
# ---------------------------------------------------------------------

def generate_excel_report(df: pd.DataFrame, output_path: str | Path) -> Path:
    output_path = Path(output_path)
    export_df = _prepare_export_df(df)

    wb = Workbook()
    ws_summary = wb.active
    ws_summary.title = "Synthèse"

    ws_summary["A1"] = "Rapport de revue d'accès"
    ws_summary["A1"].font = Font(size=14, bold=True)
    ws_summary["A2"] = f"Généré le {datetime.now().strftime('%d/%m/%Y à %H:%M')}"
    ws_summary["A2"].font = Font(italic=True, color="666666")

    ws_summary["A4"] = "Niveau de risque"
    ws_summary["B4"] = "Nombre de comptes"
    ws_summary["A4"].font = ws_summary["B4"].font = Font(bold=True)

    risk_counts = df["risk_level"].value_counts() if "risk_level" in df.columns else {}
    row = 5
    for risk, hex_color in RISK_COLORS_HEX.items():
        count = int(risk_counts.get(risk, 0))
        ws_summary[f"A{row}"] = risk
        ws_summary[f"B{row}"] = count
        ws_summary[f"A{row}"].fill = PatternFill("solid", fgColor=hex_color)
        ws_summary[f"A{row}"].font = Font(color="FFFFFF", bold=True)
        row += 1

    ws_summary[f"A{row + 1}"] = "Total comptes analysés"
    ws_summary[f"B{row + 1}"] = len(df)
    ws_summary[f"A{row + 1}"].font = Font(bold=True)

    if "is_terminated_but_active" in df.columns:
        ws_summary[f"A{row + 3}"] = "Comptes actifs d'employés partis"
        ws_summary[f"B{row + 3}"] = int(df["is_terminated_but_active"].sum())
    if "is_dormant" in df.columns:
        ws_summary[f"A{row + 4}"] = "Comptes dormants"
        ws_summary[f"B{row + 4}"] = int(df["is_dormant"].sum())

    for col, width in zip("AB", [32, 20]):
        ws_summary.column_dimensions[col].width = width

    ws = wb.create_sheet("Plan de revue")
    header_fill = PatternFill("solid", fgColor="1F2937")
    header_font = Font(color="FFFFFF", bold=True)
    thin_border = Border(*[Side(style="thin", color="D9D9D9")] * 4)

    for col_idx, col_name in enumerate(export_df.columns, 1):
        cell = ws.cell(row=1, column=col_idx, value=col_name)
        cell.fill, cell.font = header_fill, header_font
        cell.alignment = Alignment(horizontal="center")
        cell.border = thin_border

    risk_col_idx = (
        list(export_df.columns).index("Risque") + 1 if "Risque" in export_df.columns else None
    )

    for row_idx, record in enumerate(export_df.to_dict("records"), 2):
        for col_idx, (col_name, value) in enumerate(record.items(), 1):
            cell = ws.cell(row=row_idx, column=col_idx, value=value)
            cell.border = thin_border
        if risk_col_idx:
            hex_color = RISK_COLORS_HEX.get(record.get("Risque"))
            if hex_color:
                cell = ws.cell(row=row_idx, column=risk_col_idx)
                cell.fill = PatternFill("solid", fgColor=hex_color)
                cell.font = Font(color="FFFFFF", bold=True)

    for col_idx, col_name in enumerate(export_df.columns, 1):
        max_len = max([len(str(col_name))] + [len(str(v)) for v in export_df[col_name].astype(str)])
        ws.column_dimensions[get_column_letter(col_idx)].width = min(max_len + 4, 40)

    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions

    output_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(output_path)
    logger.info(f"Rapport Excel généré : {output_path}")
    return output_path


# ---------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------

def generate_pdf_report(df: pd.DataFrame, output_path: str | Path) -> Path:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    doc = SimpleDocTemplate(
        str(output_path), pagesize=landscape(A4),
        topMargin=1.5 * cm, bottomMargin=1.5 * cm, leftMargin=1.5 * cm, rightMargin=1.5 * cm,
    )
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("TitleCustom", parent=styles["Title"], fontSize=18, spaceAfter=6)
    subtitle_style = ParagraphStyle("Subtitle", parent=styles["Normal"], fontSize=10, textColor=colors.grey)

    elements = [
        Paragraph("Rapport de revue d'accès", title_style),
        Paragraph(f"Généré le {datetime.now().strftime('%d/%m/%Y à %H:%M')}", subtitle_style),
        Spacer(1, 0.6 * cm),
    ]

    risk_counts = df["risk_level"].value_counts() if "risk_level" in df.columns else {}
    summary_data = [["Indicateur", "Valeur"], ["Total comptes analysés", str(len(df))]]
    for risk in RISK_COLORS_HEX:
        summary_data.append([risk, str(int(risk_counts.get(risk, 0)))])
    if "is_terminated_but_active" in df.columns:
        summary_data.append(["Comptes actifs d'employés partis", str(int(df["is_terminated_but_active"].sum()))])

    summary_table = Table(summary_data, colWidths=[9 * cm, 4 * cm])
    summary_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1F2937")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#D9D9D9")),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F5F5F5")]),
    ]))
    elements.append(summary_table)
    elements.append(Spacer(1, 1 * cm))

    export_df = _prepare_export_df(df)
    elements.append(Paragraph("Détail des comptes (triés par risque)", styles["Heading2"]))
    elements.append(Spacer(1, 0.3 * cm))

    table_data = [list(export_df.columns)] + export_df.astype(str).values.tolist()
    detail_table = Table(table_data, repeatRows=1)

    style_commands = [
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1F2937")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 7.5),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#D9D9D9")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]

    if "Risque" in export_df.columns:
        risk_col_idx = list(export_df.columns).index("Risque")
        for row_idx, risk_value in enumerate(export_df["Risque"], 1):
            hex_color = RISK_COLORS_HEX.get(risk_value)
            if hex_color:
                style_commands.append((
                    "BACKGROUND", (risk_col_idx, row_idx), (risk_col_idx, row_idx),
                    colors.HexColor(f"#{hex_color}"),
                ))
                style_commands.append((
                    "TEXTCOLOR", (risk_col_idx, row_idx), (risk_col_idx, row_idx), colors.white,
                ))

    detail_table.setStyle(TableStyle(style_commands))
    elements.append(detail_table)

    doc.build(elements)
    logger.info(f"Rapport PDF généré : {output_path}")
    return output_path


if __name__ == "__main__":
    import sys
    from pathlib import Path as _Path
    sys.path.insert(0, str(_Path(__file__).parent.parent))

    logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")

    from ingestion.ingest import load_file
    from analysis.access_review import analyze_access

    if len(sys.argv) < 2:
        print("Usage : python -m reporting.export <chemin_fichier>")
        sys.exit(1)

    df_result = analyze_access(load_file(sys.argv[1]))
    out_dir = _Path(__file__).parent.parent / "output"
    generate_excel_report(df_result, out_dir / "rapport_revue_acces.xlsx")
    generate_pdf_report(df_result, out_dir / "rapport_revue_acces.pdf")
