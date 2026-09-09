"""
Module d'export du rapport de revue d'accès.

Génère trois livrables à partir du DataFrame analysé (sortie de
analysis/access_review.py), tous construits à partir des MÊMES calculs :

    - Un rapport Excel détaillé avec mise en forme conditionnelle par
      niveau de risque, prêt pour le suivi opérationnel.
    - Un rapport PDF de synthèse, adapté à une diffusion managériale ou
      une preuve d'audit pour la revue d'accès périodique.
    - Un rapport Word, structurellement identique au PDF, mais modifiable
      après génération (ajout de commentaires, ajustements avant envoi).
"""

from __future__ import annotations
import logging
import re
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
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

from docx import Document
from docx.shared import Pt, Cm, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.oxml.ns import qn
from docx.oxml import OxmlElement

from reporting.template_sections import (
    TEMPLATE_CONTROLS, OBJECTIVE_INTRO, OBJECTIVE_BULLETS, OBJECTIVE_CONTROL_INTRO,
    PRINCIPLES_INTRO, ACCOUNT_TYPES, CREATION_PROCESS_INTRO, CREATION_PROCESS_ITEMS,
    SECTION_IV_INTRO, DUMP_COMPLETENESS_HEADER, DUMP_COMPLETENESS_GUIDANCE,
    DUMP_COMPLETENESS_COLUMNS, CONTROL_SUBSECTIONS, CONCLUSION_HEADING,
)
from analysis.access_review import _is_active_account
from ingestion.ingest import compute_data_quality_report

logger = logging.getLogger("export")

# Police par défaut du PDF : DejaVu Sans plutôt que Helvetica (police
# intégrée à ReportLab, limitée à l'alphabet latin de base). DejaVu Sans
# couvre correctement le cyrillique, le grec et le latin étendu (accents
# de nombreuses langues africaines/européennes) — utile pour un outil
# destiné à une entreprise présente dans plusieurs pays.
# Limite assumée et documentée : DejaVu Sans NE couvre PAS l'arabe ni les
# écritures d'Asie de l'Est (chinois/japonais/coréen) — ces polices
# pèsent 15-20+ Mo chacune, trop lourd à embarquer dans ce projet. Un nom
# écrit dans l'une de ces écritures s'affichera comme un bloc illisible
# plutôt que du texte, sans faire planter la génération pour autant.
_FONTS_DIR = Path(__file__).parent.parent / "assets" / "fonts"
try:
    pdfmetrics.registerFont(TTFont("DejaVu", str(_FONTS_DIR / "DejaVuSans.ttf")))
    pdfmetrics.registerFont(TTFont("DejaVu-Bold", str(_FONTS_DIR / "DejaVuSans-Bold.ttf")))
    DEFAULT_FONT = "DejaVu"
    DEFAULT_FONT_BOLD = "DejaVu-Bold"
except Exception as e:
    logger.warning(
        f"Police DejaVu Sans introuvable ({e}), repli sur Helvetica — les "
        f"caractères hors alphabet latin de base (cyrillique, grec...) ne "
        f"s'afficheront pas correctement dans le PDF généré."
    )
    DEFAULT_FONT = DEFAULT_FONT
    DEFAULT_FONT_BOLD = DEFAULT_FONT_BOLD

RISK_COLORS_HEX = {
    "Critique": "D62728",
    "Élevé": "FF7F0E",
    "Moyen": "FFD700",
    "Faible": "2CA02C",
}

# Formulation générique associée à chaque action recommandée, pour la
# section narrative "Rapport des exceptions" — inspirée des standards du
# secteur (revue trimestrielle des accès), jamais copiée d'un document
# précis : ces recommandations sont volontairement génériques pour rester
# valables quelle que soit l'entreprise ou le système concerné.
ACTION_NARRATIVE = {
    "Révoquer immédiatement": (
        "Ces comptes restent actifs alors que la personne associée a quitté "
        "l'entreprise. Action recommandée : révocation immédiate des accès."
    ),
    "Désactiver (privilégié dormant)": (
        "Ces comptes disposent de privilèges élevés et n'ont enregistré aucune "
        "connexion depuis le seuil de dormance retenu. Action recommandée : "
        "désactivation, le niveau d'accès concerné justifie une vigilance "
        "renforcée."
    ),
    "Forcer l'expiration du mot de passe (privilégié)": (
        "Ces comptes à privilèges élevés ont un mot de passe configuré pour "
        "ne jamais expirer. Action recommandée : appliquer une politique "
        "d'expiration standard, et documenter toute exception justifiée "
        "(compte de service avec surveillance dédiée)."
    ),
    "Désactiver (dormant)": (
        "Ces comptes n'ont enregistré aucune connexion depuis le seuil de "
        "dormance retenu. Action recommandée : vérifier auprès du "
        "propriétaire métier, puis désactiver si l'usage n'est plus justifié."
    ),
    "Exiger un changement de mot de passe": (
        "Le mot de passe de ces comptes n'a pas été renouvelé depuis le seuil "
        "retenu. Action recommandée : forcer le changement à la prochaine "
        "connexion."
    ),
    "Identifier un owner": (
        "Aucun manager ou propriétaire métier n'est identifié pour ces "
        "comptes. Action recommandée : désigner un responsable chargé de "
        "valider la légitimité de l'accès."
    ),
    "Vérifier avec le propriétaire technique (compte de service)": (
        "Ces comptes de service n'ont enregistré aucune activité depuis le "
        "seuil de dormance retenu. Action recommandée : vérifier auprès du "
        "propriétaire technique s'ils sont toujours utilisés par un "
        "processus automatisé avant toute décision, une désactivation "
        "directe pouvant casser un traitement encore actif."
    ),
    "Fusionner les doublons (ne garder qu'un compte actif)": (
        "Plusieurs comptes actifs semblent appartenir à la même personne "
        "sur le même système. Action recommandée : confirmer le doublon "
        "auprès du titulaire, puis désactiver tous les comptes superflus "
        "pour n'en garder qu'un seul actif."
    ),
}

DISPLAY_COLUMNS = [
    ("username", "Compte"),
    ("user_id", "ID employé"),
    ("full_name", "Nom"),
    ("department", "Département"),
    ("system", "Système"),
    ("manager", "Manager"),
    ("account_status", "Statut compte"),
    ("employee_status", "Statut RH"),
    ("days_since_last_login", "Jours sans connexion"),
    ("days_since_password_change", "Jours sans changement MDP"),
    ("is_privileged_flag", "Privilégié"),
    ("has_non_expiring_password", "MDP n'expire jamais"),
    ("review_action", "Action recommandée"),
    ("risk_score", "Score"),
    ("risk_level", "Risque"),
]

# Libellés pour les colonnes justificatives des tableaux par contrôle,
# non couvertes par DISPLAY_COLUMNS (valeurs brutes plutôt que calculées).
_EXTRA_COLUMN_LABELS = {
    "last_login_date": "Dernière connexion (brute)",
    "account_created_date": "Date de création",
    "password_last_set": "Dernier changement MDP (brut)",
    "role": "Rôle",
    "password_status": "Statut mot de passe",
}
ALL_COLUMN_LABELS = {**dict(DISPLAY_COLUMNS), **_EXTRA_COLUMN_LABELS}

# Colonnes justificatives par contrôle : celles qui permettent de VÉRIFIER
# pourquoi un compte est listé, pas seulement l'action qui en résulte —
# ex. pour "Dormant", voir la dernière connexion réelle et son ancienneté
# en jours, pas seulement l'action "Désactiver". Clé = même clé que
# CONTROL_SUBSECTIONS (booléenne, ou "_created"/"_deleted"/...).
CONTROL_TABLE_COLUMNS = {
    "is_dormant": ["username", "full_name", "system", "account_status", "last_login_date", "days_since_last_login", "review_action"],
    "is_test_account": ["username", "full_name", "system", "account_status", "review_action"],
    "_active_count": ["username", "full_name", "system", "account_status", "last_login_date", "review_action"],
    "is_never_used": ["username", "full_name", "system", "account_created_date", "last_login_date", "review_action"],
    "is_service_account": ["username", "full_name", "system", "account_status", "last_login_date", "review_action"],
    "is_duplicate_account": ["username", "full_name", "system", "department", "account_status", "review_action"],
    "is_non_compliant_naming": ["username", "full_name", "system", "account_status", "review_action"],
    "_created": ["username", "full_name", "system", "account_created_date", "account_status", "review_action"],
    "_profile_modified": ["username", "full_name", "system", "role", "account_status", "review_action"],
    "_reactivated": ["username", "full_name", "system", "account_status", "review_action"],
    "_deleted": ["username", "full_name", "system"],  # n'existe plus dans le cycle courant : pas d'action à afficher
    "is_password_stale": ["username", "full_name", "system", "password_last_set", "days_since_password_change", "review_action"],
    "is_privileged_flag": ["username", "full_name", "system", "role", "account_status", "review_action"],
    "is_terminated_but_active": ["username", "full_name", "system", "employee_status", "account_status", "review_action"],
}


_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]")


def _strip_control_characters(df: pd.DataFrame) -> pd.DataFrame:
    """
    Retire les caractères de contrôle invisibles (ex. NULL, caractères
    non imprimables) des colonnes texte — présents parfois dans des
    exports mal nettoyés (copier-coller, bug d'encodage). Sans ce
    nettoyage, openpyxl refuse purement et simplement d'écrire la
    cellule et fait planter tout l'export Excel ; ReportLab, lui, les
    ignore silencieusement (pas de plantage, mais autant nettoyer pour
    les deux formats de façon cohérente).
    """
    df = df.copy()
    for col in df.columns:
        df[col] = df[col].apply(
            lambda v: _CONTROL_CHARS_RE.sub("", v) if isinstance(v, str) else v
        )
    return df


def _prepare_export_df(df: pd.DataFrame) -> pd.DataFrame:
    available = [(col, label) for col, label in DISPLAY_COLUMNS if col in df.columns]
    export_df = df[[col for col, _ in available]].copy()
    export_df.columns = [label for _, label in available]
    export_df = _strip_control_characters(export_df)

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
    if "is_password_stale" in df.columns:
        ws_summary[f"A{row + 5}"] = "Mots de passe périmés"
        ws_summary[f"B{row + 5}"] = int(df["is_password_stale"].sum())
    if "is_privileged_flag" in df.columns and "has_non_expiring_password" in df.columns:
        ws_summary[f"A{row + 6}"] = "Comptes privilégiés à mot de passe n'expirant jamais"
        ws_summary[f"B{row + 6}"] = int((df["is_privileged_flag"] & df["has_non_expiring_password"]).sum())
    if "is_duplicate_account" in df.columns:
        ws_summary[f"A{row + 7}"] = "Comptes en doublon"
        ws_summary[f"B{row + 7}"] = int(df["is_duplicate_account"].sum())

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

def _current_quarter_label() -> str:
    now = datetime.now()
    quarter = (now.month - 1) // 3 + 1
    return f"T{quarter} {now.year}"


# Poids relatifs de largeur par colonne (les colonnes non listées ont un
# poids par défaut de 1.0). "Action recommandée" et "Nom" sont plus larges
# car elles contiennent le texte le plus long — sans ça, ReportLab
# dimensionne les colonnes selon leur seul contenu, sans jamais tenir
# compte de la largeur réelle de la page, d'où un tableau qui déborde.
COLUMN_WIDTH_WEIGHTS = {
    "Compte": 1.1,
    "Nom": 1.4,
    "Département": 1.0,
    "Système": 1.0,
    "Manager": 1.0,
    "Statut compte": 0.9,
    "Statut RH": 0.9,
    "Jours sans connexion": 0.9,
    "Jours sans changement MDP": 1.1,
    "Privilégié": 0.7,
    "MDP n'expire jamais": 1.0,
    "Action recommandée": 2.2,
    "Risque": 0.8,
    "Dernière connexion (brute)": 1.3,
    "Date de création": 1.1,
    "Dernier changement MDP (brut)": 1.3,
    "Rôle": 1.2,
    "Statut mot de passe": 1.0,
}
# Colonnes dont le texte doit pouvoir revenir à la ligne plutôt que
# déborder ou être tronqué.
WRAP_COLUMNS = {
    "Compte", "ID employé", "Nom", "Département", "Système", "Manager",
    "Statut compte", "Statut RH", "Action recommandée",
    "Dernière connexion (brute)", "Date de création",
    "Dernier changement MDP (brut)", "Rôle", "Statut mot de passe",
}


def _compute_column_widths(columns: list[str], available_width: float) -> list[float]:
    weights = [COLUMN_WIDTH_WEIGHTS.get(col, 1.0) for col in columns]
    total_weight = sum(weights)
    return [available_width * w / total_weight for w in weights]


def _risk_styled_table(export_df: pd.DataFrame, available_width: float) -> Table:
    """Construit une table stylée (en-tête sombre, lignes alternées, cellule
    Risque colorée), avec des largeurs de colonnes proportionnelles à la
    largeur réelle de la page plutôt qu'au seul contenu, et un retour à la
    ligne automatique — sur les en-têtes ET sur les colonnes de texte long
    (sans quoi un libellé de colonne trop long déborde silencieusement sur
    la colonne voisine plutôt que de simplement passer à la ligne)."""
    cell_style = ParagraphStyle("Cell", fontSize=7.5, leading=9, fontName=DEFAULT_FONT)
    header_style = ParagraphStyle(
        "CellHeader", fontSize=7.5, leading=9, fontName=DEFAULT_FONT_BOLD, textColor=colors.white,
    )
    columns = list(export_df.columns)
    col_widths = _compute_column_widths(columns, available_width)

    header_row = [Paragraph(str(col), header_style) for col in columns]
    data_rows = []
    # .astype(str) ne convertit pas les valeurs manquantes (NaN) en texte —
    # elles restent des float et font planter Paragraph() plus bas, qui
    # exige une vraie chaîne. On les remplace explicitement avant conversion.
    for record in export_df.fillna("").astype(str).values.tolist():
        row = []
        for col_name, value in zip(columns, record):
            if col_name in WRAP_COLUMNS:
                row.append(Paragraph(value, cell_style))
            else:
                row.append(value)
        data_rows.append(row)

    table = Table([header_row] + data_rows, repeatRows=1, colWidths=col_widths)

    style_commands = [
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1F2937")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), DEFAULT_FONT_BOLD),
        ("FONTNAME", (0, 1), (-1, -1), DEFAULT_FONT),
        ("FONTSIZE", (0, 0), (-1, -1), 7.5),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#D9D9D9")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F9F9F9")]),
    ]
    if "Risque" in export_df.columns:
        risk_col_idx = columns.index("Risque")
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
    table.setStyle(TableStyle(style_commands))
    return table


def _build_exceptions_section(df: pd.DataFrame, section_style, exception_style, action_style) -> list:
    """
    Construit la section narrative "Rapport des exceptions" : les comptes
    signalés sont regroupés par (système, action recommandée), puis
    numérotés "Exception N : ... Action : ...", au format d'un rapport
    d'audit classique — plutôt que le tableau brut de la section suivante.
    """
    elements = [Paragraph("Rapport des exceptions", section_style)]

    if "system" not in df.columns or "review_action" not in df.columns:
        elements.append(Paragraph(
            "Champs insuffisants pour générer le rapport des exceptions "
            "(système et action recommandée requis).",
            action_style,
        ))
        return elements

    flagged = df[df["review_action"] != "Aucune action"]
    if flagged.empty:
        elements.append(Paragraph("Aucune exception à signaler sur ce cycle.", action_style))
        return elements

    counter = 1
    for system_name, system_group in flagged.groupby("system"):
        for action, action_group in system_group.groupby("review_action"):
            count = len(action_group)
            elements.append(Paragraph(
                f"<b>Exception {counter} — {system_name} :</b> {count} compte(s) "
                f"avec le statut « {action} ».",
                exception_style,
            ))
            narrative = ACTION_NARRATIVE.get(
                action, "Voir la sous-section de contrôle correspondante ci-dessus pour le détail nominatif."
            )
            elements.append(Paragraph(narrative, action_style))
            counter += 1

    return elements



def _build_dump_completeness_table(df: pd.DataFrame, available_width: float) -> Table:
    """Tableau du contrôle 1, avec les libellés de colonnes exacts du
    template ('User logon (User ID)', 'User creation DATE', etc.)."""
    rows = [["Field", "Status"]]
    for label, candidates in DUMP_COMPLETENESS_COLUMNS:
        present = any(c in df.columns and df[c].notna().any() for c in candidates)
        rows.append([label, "OK" if present else "NOK"])
    table = Table(rows, colWidths=[available_width * 0.7, available_width * 0.3])
    style_commands = [
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1F2937")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, -1), DEFAULT_FONT),
        ("FONTNAME", (0, 0), (-1, 0), DEFAULT_FONT_BOLD),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#D9D9D9")),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]
    for i, (label, candidates) in enumerate(DUMP_COMPLETENESS_COLUMNS, 1):
        present = any(c in df.columns and df[c].notna().any() for c in candidates)
        color = colors.HexColor("#0E6E57") if present else colors.HexColor("#A13D2E")
        style_commands.append(("TEXTCOLOR", (1, i), (1, i), color))
        style_commands.append(("FONTNAME", (1, i), (1, i), DEFAULT_FONT_BOLD))
    table.setStyle(TableStyle(style_commands))
    return table


def compute_control_coverage(df: pd.DataFrame, comparison_stats: dict) -> list[tuple]:
    """
    Calcul PUR (aucun rendu) de l'état des 18 contrôles — réutilisé à la
    fois par le PDF (_build_control_summary_table) et le dashboard
    (Control Coverage), pour ne jamais dupliquer cette logique.
    Retourne une liste de tuples (numéro, titre, statut, affichage_compte).
    """
    rows = []
    dump_ok = all(
        any(c in df.columns and df[c].notna().any() for c in candidates)
        for _, candidates in DUMP_COMPLETENESS_COLUMNS
    )
    rows.append((1, "Dump completeness and accuracy", "OK" if dump_ok else "⚠️", "—"))

    for number, title, _, key in CONTROL_SUBSECTIONS:
        count = None
        if key is None:
            status, count_display = "N/A", "—"
        elif key == "_active_count":
            if "account_status" in df.columns:
                count = int(df["account_status"].apply(_is_active_account).sum())
            status = "OK"
            count_display = str(count) if count is not None else "—"
        elif key in ("_created", "_reactivated", "_deleted", "_profile_modified"):
            value = comparison_stats.get(key.lstrip("_"))
            if value is None:
                status, count_display = "N/A", "—"
            else:
                status, count_display = ("⚠️" if value > 0 else "OK"), str(value)
        elif key in df.columns:
            count = int(df[key].sum())
            status = "⚠️" if count > 0 else "OK"
            count_display = str(count)
        else:
            status, count_display = "N/A", "—"
        rows.append((number, title, status, count_display))
    return rows


def _build_control_summary_table(df: pd.DataFrame, comparison_stats: dict, available_width: float) -> Table:
    """
    Vue d'ensemble compacte des 18 contrôles — une ligne par contrôle,
    statut OK/⚠️/N/A et le compte associé, pour une lecture en un coup
    d'œil avant le détail verbeux des sous-sections IV.2 à IV.18.
    """
    coverage = compute_control_coverage(df, comparison_stats)
    rows = [["N°", "Contrôle", "Résultat", "Anomalies"]]
    for number, title, status, count_display in coverage:
        rows.append([str(number), title, status, count_display])

    table = Table(rows, colWidths=[available_width * w for w in (0.06, 0.52, 0.14, 0.28)], repeatRows=1)
    style_commands = [
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1F2937")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, -1), DEFAULT_FONT),
        ("FONTNAME", (0, 0), (-1, 0), DEFAULT_FONT_BOLD),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#D9D9D9")),
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F9F9F9")]),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]
    for i, row in enumerate(rows[1:], 1):
        color = {"OK": colors.HexColor("#0E6E57"), "⚠️": colors.HexColor("#A13D2E")}.get(row[2], colors.grey)
        style_commands.append(("TEXTCOLOR", (2, i), (2, i), color))
        style_commands.append(("FONTNAME", (2, i), (2, i), DEFAULT_FONT_BOLD))
    table.setStyle(TableStyle(style_commands))
    return table


def _build_capped_account_table(
    subset_df: pd.DataFrame, available_width: float,
    columns: list[str] | None = None,
) -> list:
    """
    Tableau complet des comptes concernés par un contrôle donné, avec les
    colonnes justificatives propres à CE contrôle (ex. dernière connexion
    réelle pour "Dormant", pas seulement l'action qui en résulte) ET
    l'action recommandée — pour que la revue soit directement exploitable
    à partir de cette seule section, sans plafond : ce sont les 18
    sections qui sont effectivement revues, la complétude prime ici sur
    la longueur du document. Largeurs de colonnes proportionnelles et
    retour à la ligne automatique (même infrastructure que le détail
    principal) — sans quoi un en-tête un peu long chevauche son voisin.
    """
    default_cols = ["username", "full_name", "system", "review_action"]
    cols = [c for c in (columns or default_cols) if c in subset_df.columns]
    if not cols or subset_df.empty:
        return []
    labels = [ALL_COLUMN_LABELS.get(c, c) for c in cols]
    col_widths = _compute_column_widths(labels, available_width)

    cell_style = ParagraphStyle("CapCell", fontSize=7.5, leading=9, fontName=DEFAULT_FONT)
    header_style = ParagraphStyle(
        "CapHeader", fontSize=7.5, leading=9, fontName=DEFAULT_FONT_BOLD, textColor=colors.white,
    )
    header_row = [Paragraph(label, header_style) for label in labels]
    data_rows = []
    for record in subset_df[cols].fillna("").astype(str).values.tolist():
        row = []
        for label, value in zip(labels, record):
            row.append(Paragraph(value, cell_style) if label in WRAP_COLUMNS else value)
        data_rows.append(row)

    table = Table([header_row] + data_rows, colWidths=col_widths, repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1F2937")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, -1), DEFAULT_FONT),
        ("FONTNAME", (0, 0), (-1, 0), DEFAULT_FONT_BOLD),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#D9D9D9")),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F9F9F9")]),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    return [table]


def _build_control_subsections(
    df: pd.DataFrame, comparison_stats: dict, section_style, system_style, note_style, action_style,
    available_width: float, previous_df: pd.DataFrame | None = None,
) -> list:
    """
    Reproduit fidèlement les sous-sections 2 à 18 de la section IV du
    template — chacune avec son titre exact et, quand le template en
    fournit une, sa consigne ("Guidance") reproduite mot pour mot. Chaque
    sous-section affiche le compte réel calculé quand l'outil en est
    capable — avec la LISTE nominative des comptes concernés ET les
    colonnes justificatives propres à ce contrôle (voir
    CONTROL_TABLE_COLUMNS), pas seulement l'action qui en résulte, pour
    rester vérifiable en revue d'audit — ou "N/A" avec une explication
    quand une configuration propre à l'entreprise serait nécessaire
    (convention de nommage, marqueur de compte de test...) — jamais un
    chiffre inventé.
    """
    elements = []
    for number, title, guidance, key in CONTROL_SUBSECTIONS:
        elements.append(Paragraph(f"{number}.{title}", system_style))
        if guidance:
            elements.append(Paragraph(guidance, note_style))

        count = None
        note = None
        subset = None
        if key is None:
            note = "N/A — nécessite une configuration propre à l'entreprise, non déductible des seules données ingérées."
        elif key == "_active_count":
            if "account_status" in df.columns:
                subset = df[df["account_status"].apply(_is_active_account)]
                count = len(subset)
            else:
                note = "N/A — colonne 'account_status' absente."
        elif key == "_deleted":
            # Un compte supprimé, par définition, n'existe plus dans le
            # fichier ACTUEL — le chercher dans `df` renverrait toujours
            # zéro résultat. Il ne peut être retrouvé (pour afficher ses
            # attributs) que dans la revue PRÉCÉDENTE.
            value = comparison_stats.get("deleted")
            if value is None:
                note = "N/A — aucune revue précédente fournie pour établir la comparaison."
            else:
                count = value
                names = comparison_stats.get("deleted_accounts") or []
                if names and previous_df is not None and "username" in previous_df.columns:
                    subset = previous_df[previous_df["username"].astype(str).isin(names)]
        elif key in ("_created", "_reactivated", "_profile_modified"):
            stat_key = key.lstrip("_")
            value = comparison_stats.get(stat_key)
            if value is None:
                note = "N/A — aucune revue précédente fournie pour établir la comparaison."
            else:
                count = value
                names = comparison_stats.get(f"{stat_key}_accounts") or []
                if names and "username" in df.columns:
                    subset = df[df["username"].astype(str).isin(names)]
        elif key in df.columns:
            subset = df[df[key] == True]  # noqa: E712 (comparaison explicite voulue sur une colonne booléenne)
            count = len(subset)
        else:
            note = f"N/A — colonne '{key}' absente des données ingérées."

        if count is not None:
            elements.append(Paragraph(f"<b>{count}</b> compte(s) concerné(s).", action_style))
            if subset is not None and len(subset):
                table_cols = CONTROL_TABLE_COLUMNS.get(key)
                elements.extend(_build_capped_account_table(subset, available_width, columns=table_cols))
        else:
            elements.append(Paragraph(note, note_style))
        elements.append(Spacer(1, 0.25 * cm))
    return elements


def _build_review_comparison_section(df: pd.DataFrame, previous_df, section_style, note_style, available_width):
    """
    Section 'a. Summary of the review' : répartition des comptes par
    statut, comparée au cycle précédent si `previous_df` est fourni —
    calculée à partir des données réelles, pas déclarative.

    Retourne (elements, stats) où `stats` est un dict {created, deleted,
    reactivated, profile_modified} réutilisé par la section IV pour
    éviter de recalculer la même comparaison deux fois.
    """
    stats = {
        "created": None, "deleted": None, "reactivated": None,
        "profile_modified": None, "privilege_escalation": None,
        "created_accounts": None, "deleted_accounts": None,
        "reactivated_accounts": None, "profile_modified_accounts": None,
        "privilege_escalation_accounts": None,
    }
    elements = [Paragraph("a. Summary of the review", section_style)]
    if "account_status" not in df.columns:
        elements.append(Paragraph(
            "Colonne 'account_status' absente : répartition par statut indisponible.", note_style,
        ))
        return elements, stats

    current_counts = df["account_status"].value_counts()
    if previous_df is not None and "account_status" in previous_df.columns:
        previous_counts = previous_df["account_status"].value_counts()
        elements.append(Paragraph(
            "The review of the application accounts covers a total of accounts distributed as follows:",
            note_style,
        ))
        statuses = sorted(set(current_counts.index) | set(previous_counts.index))
        rows = [["Type of Users", "Previous review", "Current review", "Variation"]]
        for status in statuses:
            prev = int(previous_counts.get(status, 0))
            curr = int(current_counts.get(status, 0))
            rows.append([str(status), str(prev), str(curr), f"{curr - prev:+d}"])
        rows.append(["TOTAL", str(len(previous_df)), str(len(df)), f"{len(df) - len(previous_df):+d}"])
        comp_table = Table(rows, colWidths=[available_width * w for w in (0.35, 0.2, 0.2, 0.25)])
        comp_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#DCE6F1")),
            ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#DCE6F1")),
            ("FONTNAME", (0, 0), (-1, -1), DEFAULT_FONT),
            ("FONTNAME", (0, 0), (-1, 0), DEFAULT_FONT_BOLD),
            ("FONTNAME", (0, -1), (-1, -1), DEFAULT_FONT_BOLD),
            ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#D9D9D9")),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ]))
        elements.append(comp_table)
        elements.append(Spacer(1, 0.2 * cm))

        # Comparaison nominative : créés / supprimés / réactivés / profils modifiés
        if "username" in df.columns and "username" in previous_df.columns:
            key_col = "username"
            current_keys = set(df[key_col].dropna())
            previous_keys = set(previous_df[key_col].dropna())
            created = current_keys - previous_keys
            deleted = previous_keys - current_keys
            common = current_keys & previous_keys

            reactivated_accounts = []
            profile_modified_accounts = []
            escalated_accounts = []
            if common:
                curr_idx = df.set_index(key_col)
                prev_idx = previous_df.set_index(key_col)
                for uname in common:
                    curr_row = curr_idx.loc[uname]
                    prev_row = prev_idx.loc[uname]
                    if isinstance(curr_row, pd.DataFrame):
                        curr_row = curr_row.iloc[0]
                    if isinstance(prev_row, pd.DataFrame):
                        prev_row = prev_row.iloc[0]
                    if "account_status" in df.columns:
                        was_inactive = not _is_active_account(prev_row.get("account_status"))
                        is_active_now = _is_active_account(curr_row.get("account_status"))
                        if was_inactive and is_active_now:
                            reactivated_accounts.append(str(uname))
                    if "role" in df.columns:
                        if str(prev_row.get("role")) != str(curr_row.get("role")):
                            profile_modified_accounts.append(str(uname))
                    # Escalade de privilège : signal plus fort qu'un simple
                    # "profil modifié" générique — un compte qui devient
                    # privilégié entre deux revues mérite d'être identifié
                    # nommément, pas seulement compté avec les autres
                    # modifications de profil.
                    was_privileged = bool(prev_row.get("is_privileged_flag", False))
                    is_privileged_now = bool(curr_row.get("is_privileged_flag", False))
                    if not was_privileged and is_privileged_now:
                        escalated_accounts.append(str(uname))

            reactivated, profile_modified = len(reactivated_accounts), len(profile_modified_accounts)
            stats.update({
                "created": len(created), "deleted": len(deleted),
                "reactivated": reactivated, "profile_modified": profile_modified,
                "privilege_escalation": len(escalated_accounts),
                "created_accounts": sorted(created), "deleted_accounts": sorted(deleted),
                "reactivated_accounts": reactivated_accounts,
                "profile_modified_accounts": profile_modified_accounts,
                "privilege_escalation_accounts": escalated_accounts,
            })
            diff_rows = [
                ["Indicator", "Count"],
                ["Accounts created", str(len(created))],
                ["Accounts deleted", str(len(deleted))],
                ["Reactivated accounts", str(reactivated)],
                ["Profile Modified", str(profile_modified)],
                ["Privilege Escalation", str(len(escalated_accounts))],
            ]
            diff_table = Table(diff_rows, colWidths=[available_width * 0.6, available_width * 0.4])
            diff_table.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1F2937")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, -1), DEFAULT_FONT),
                ("FONTNAME", (0, 0), (-1, 0), DEFAULT_FONT_BOLD),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#D9D9D9")),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]))
            elements.append(diff_table)
            if escalated_accounts:
                elements.append(Spacer(1, 0.2 * cm))
                elements.append(Paragraph(
                    "<b>Privilege Escalation — comptes concernés :</b> "
                    + ", ".join(escalated_accounts[:20])
                    + (f" (+{len(escalated_accounts) - 20} autre(s))" if len(escalated_accounts) > 20 else ""),
                    note_style,
                ))
    else:
        elements.append(Paragraph(
            "The review of the application accounts covers a total of accounts distributed as "
            "follows (aucune revue précédente fournie pour comparaison) :",
            note_style,
        ))
        rows = [["Type of Users", "Current review"]]
        for status, count in current_counts.items():
            rows.append([str(status), str(int(count))])
        rows.append(["TOTAL", str(len(df))])
        comp_table = Table(rows, colWidths=[available_width * 0.6, available_width * 0.4])
        comp_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#DCE6F1")),
            ("FONTNAME", (0, 0), (-1, -1), DEFAULT_FONT),
            ("FONTNAME", (0, 0), (-1, 0), DEFAULT_FONT_BOLD),
            ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#D9D9D9")),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
        ]))
        elements.append(comp_table)
    return elements, stats


# ---------------------------------------------------------------------
# WORD (.docx) — même structure et mêmes calculs que le PDF, pour un
# document modifiable après génération (commentaires, ajustements avant
# envoi à un auditeur). Aucune logique de détection dupliquée : toutes
# les données viennent des mêmes fonctions déjà utilisées par le PDF.
# ---------------------------------------------------------------------

def _docx_shade_cell(cell, hex_color: str) -> None:
    """python-docx n'a pas d'API native pour l'ombrage de cellule —
    nécessite une manipulation XML directe."""
    shd = OxmlElement("w:shd")
    shd.set(qn("w:fill"), hex_color)
    cell._tc.get_or_add_tcPr().append(shd)


def _docx_set_cell(cell, text: str, bold: bool = False, color: RGBColor | None = None, size: int = 9) -> None:
    cell.text = ""
    run = cell.paragraphs[0].add_run(str(text))
    run.bold = bold
    run.font.size = Pt(size)
    if color:
        run.font.color.rgb = color


def _docx_add_table(doc, rows: list, col_widths_cm: list[float] | None = None, header: bool = True) -> None:
    if not rows:
        return
    table = doc.add_table(rows=len(rows), cols=len(rows[0]))
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.style = "Table Grid"
    for i, row in enumerate(rows):
        for j, value in enumerate(row):
            cell = table.cell(i, j)
            if header and i == 0:
                _docx_set_cell(cell, value, bold=True, color=RGBColor(0xFF, 0xFF, 0xFF), size=9)
                _docx_shade_cell(cell, "1F2937")
            else:
                _docx_set_cell(cell, value, size=8.5)
            if col_widths_cm:
                cell.width = Cm(col_widths_cm[j])


def generate_word_report(
    df: pd.DataFrame,
    output_path: str | Path,
    period: str | None = None,
    dormant_threshold_days: int = 90,
    prepared_by: str | None = None,
    reviewed_by: str | None = None,
    approved_by: str | None = None,
    department: str | None = None,
    editor: str | None = None,
    application_scope: str | None = None,
    document_version: str = "1.0",
    previous_df: pd.DataFrame | None = None,
    logo_path: str | Path | None = None,
) -> Path:
    """
    Génère le même rapport que generate_pdf_report, au format Word plutôt
    que PDF — pour permettre des modifications manuelles après coup
    (commentaires, ajustements avant envoi à un auditeur). Mêmes
    paramètres, mêmes calculs, structure identique (I à V, IV.1-18 avec
    tableau nominatif par contrôle, qualité des données, résumé
    exécutif...) ; seul le moteur de rendu change.
    """
    output_path = Path(output_path)
    period_label = period or f"T{(datetime.now().month - 1) // 3 + 1} {datetime.now().year}"
    # Word (contrairement à ReportLab) rejette purement et simplement les
    # caractères de contrôle avec une exception XML — nettoyage requis
    # avant toute écriture, comme déjà fait pour Excel.
    df = _strip_control_characters(df)
    if previous_df is not None:
        previous_df = _strip_control_characters(previous_df)
    doc = Document()

    # Marges resserrées pour laisser de la place aux tableaux larges
    for section in doc.sections:
        section.left_margin = Cm(1.5)
        section.right_margin = Cm(1.5)

    if logo_path:
        logo_path = Path(logo_path)
        if logo_path.exists():
            try:
                p = doc.add_paragraph()
                p.alignment = WD_ALIGN_PARAGRAPH.CENTER
                p.add_run().add_picture(str(logo_path), height=Cm(1.8))
            except Exception as e:
                logger.warning(f"Logo non inséré (Word) : {e}")
        else:
            logger.warning(f"Chemin de logo introuvable, document Word généré sans logo : {logo_path}")

    # ---- En-tête officiel ----
    _docx_add_table(doc, [
        [department or "TECHNOLOGY DEPARTMENT", "Editor: " + (editor or "[FULL NAME]")],
        ["REVIEW OF THE APPLICATION ACCOUNTS", f"Version {document_version}"],
        ["Scope: " + (application_scope or "[Application Name]"), "ISM"],
        ["", period_label],
    ], header=False)

    title = doc.add_heading("APPLICATION ACCOUNTS REVIEW", level=1)
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER

    doc.add_heading("Baseline evidence of the review", level=2)
    doc.add_paragraph("Data source: Email or automated reception")
    doc.add_paragraph(f"Date of extraction: {datetime.now().strftime('%d/%m/%Y')}")
    doc.add_paragraph(f"Review date: {datetime.now().strftime('%d/%m/%Y')}")

    _docx_add_table(doc, [
        ["Version", "Created / Edited", "By", "Comment"],
        [document_version, datetime.now().strftime("%d/%m/%Y"), editor or "[SYSTEM OWNER FULL NAME]", "N/A"],
    ])
    doc.add_paragraph()
    _docx_add_table(doc, [
        ["Distribution", "Department/Role", "Action", "Information"],
        [department or "OWNER DEPARTMENT", "SYSTEM OWNER ROLE", "X", "X"],
        ["ENTERPRISE INFORMATION SECURITY", "ENTERPRISE INFORMATION SECURITY", "", "X"],
    ])
    doc.add_paragraph()

    doc.add_heading("VALIDATION", level=2)
    placeholder = "[TO BE COMPLETED]"
    _docx_add_table(doc, [
        ["SYSTEM OWNER:", "MANAGER:", "SENIOR MANAGER:"],
        [prepared_by or placeholder, reviewed_by or placeholder, approved_by or placeholder],
        ["[SIGNATURE - DATE]", "[SIGNATURE - DATE]", "[SIGNATURE - DATE]"],
    ], header=False)
    doc.add_paragraph()
    _docx_add_table(doc, [["CTIO:"], ["[FULL NAME]"], ["[SIGNATURE - DATE]"]], header=False)
    doc.add_paragraph()

    # ---- I. OBJECTIF ----
    doc.add_heading("I. OBJECTIF", level=1)
    doc.add_paragraph(OBJECTIVE_INTRO)
    for b in OBJECTIVE_BULLETS:
        doc.add_paragraph(b, style="List Bullet")
    doc.add_paragraph(OBJECTIVE_CONTROL_INTRO)
    controls_rows = [["SN", "Control", "Control Description/Expectations"]]
    for sn, name, desc in TEMPLATE_CONTROLS:
        controls_rows.append([str(sn), name, desc.replace("\n", " ")])
    _docx_add_table(doc, controls_rows, col_widths_cm=[1, 4, 12])
    doc.add_paragraph()

    # ---- II. PRINCIPLES ----
    doc.add_heading("II. PRINCIPLES OF APPLICATION ACCOUNT CREATION", level=1)
    doc.add_paragraph(PRINCIPLES_INTRO)
    doc.add_heading("1. Types of Accounts Created", level=2)
    for label, desc in ACCOUNT_TYPES:
        p = doc.add_paragraph()
        p.add_run(label).bold = True
        p.add_run(desc)
    doc.add_heading("2. Account Creation Process", level=2)
    doc.add_paragraph(CREATION_PROCESS_INTRO)
    for label, desc in CREATION_PROCESS_ITEMS:
        p = doc.add_paragraph()
        p.add_run(label).bold = True
        p.add_run(desc)

    # ---- III. REVIEW DETAILS ----
    doc.add_heading("III. REVIEW DETAILS", level=1)
    doc.add_paragraph(
        f"Méthodologie : un compte est considéré « dormant » sans connexion depuis plus de "
        f"{dormant_threshold_days} jours — y compris un compte n'ayant jamais enregistré la "
        f"moindre connexion depuis sa création. Cet outil ne recommande jamais la suppression "
        f"d'un compte, uniquement sa désactivation — réversible, et applicable sans historique "
        f"préalable."
    )

    # Réutilise le calcul de comparaison déjà construit pour le PDF — même
    # source de données, pas de logique dupliquée. Les éléments ReportLab
    # retournés sont ignorés ici, seul le dict `stats` (pur) est utilisé.
    _dummy_style = ParagraphStyle("Dummy")
    _, comparison_stats = _build_review_comparison_section(df, previous_df, _dummy_style, _dummy_style, 100)

    doc.add_heading("a. Summary of the review", level=2)
    if "account_status" in df.columns:
        current_counts = df["account_status"].value_counts()
        if previous_df is not None and "account_status" in previous_df.columns:
            previous_counts = previous_df["account_status"].value_counts()
            statuses = sorted(set(current_counts.index) | set(previous_counts.index))
            summary_rows = [["Type of Users", "Previous review", "Current review", "Variation"]]
            for status in statuses:
                prev, curr = int(previous_counts.get(status, 0)), int(current_counts.get(status, 0))
                summary_rows.append([str(status), str(prev), str(curr), f"{curr - prev:+d}"])
            summary_rows.append(["TOTAL", str(len(previous_df)), str(len(df)), f"{len(df) - len(previous_df):+d}"])
            _docx_add_table(doc, summary_rows)
            doc.add_paragraph()
            _docx_add_table(doc, [
                ["Indicator", "Count"],
                ["Accounts created", str(comparison_stats["created"])],
                ["Accounts deleted", str(comparison_stats["deleted"])],
                ["Reactivated accounts", str(comparison_stats["reactivated"])],
                ["Profile Modified", str(comparison_stats["profile_modified"])],
                ["Privilege Escalation", str(comparison_stats["privilege_escalation"])],
            ])
            escalated = comparison_stats.get("privilege_escalation_accounts") or []
            if escalated:
                names_text = ", ".join(escalated[:20])
                if len(escalated) > 20:
                    names_text += f" (+{len(escalated) - 20} autre(s))"
                p = doc.add_paragraph()
                p.add_run("Privilege Escalation — comptes concernés : ").bold = True
                p.add_run(names_text)
        else:
            rows = [["Type of Users", "Current review"]]
            for status, count in current_counts.items():
                rows.append([str(status), str(int(count))])
            rows.append(["TOTAL", str(len(df))])
            _docx_add_table(doc, rows)
    doc.add_paragraph()

    # ---- IV. ACCOUNT DETAILS BY CONTROL ----
    doc.add_heading("IV. ACCOUNT DETAILS BY CONTROL", level=1)
    doc.add_paragraph(SECTION_IV_INTRO)
    clarif_p = doc.add_paragraph(
        "Note : la colonne « Action recommandée » reflète toujours l'action prioritaire globale "
        "du compte (tous contrôles confondus), pas nécessairement la raison précise de sa présence "
        "dans la sous-section en cours — un compte peut apparaître dans plusieurs sections à la fois."
    )
    clarif_p.runs[0].italic = True
    clarif_p.runs[0].font.size = Pt(8.5)

    doc.add_heading("Control Summary", level=2)
    summary_rows = [["N°", "Contrôle", "Résultat", "Anomalies"]]
    dump_ok = all(
        any(c in df.columns and df[c].notna().any() for c in candidates)
        for _, candidates in DUMP_COMPLETENESS_COLUMNS
    )
    summary_rows.append(["1", "Dump completeness and accuracy", "OK" if dump_ok else "⚠", "—"])
    for number, ctrl_title, _, key in CONTROL_SUBSECTIONS:
        if key is None:
            status, count_display = "N/A", "—"
        elif key == "_active_count":
            status = "OK"
            count_display = str(int(df["account_status"].apply(_is_active_account).sum())) if "account_status" in df.columns else "—"
        elif key in ("_created", "_deleted", "_reactivated", "_profile_modified"):
            value = comparison_stats.get(key.lstrip("_"))
            status, count_display = ("N/A", "—") if value is None else (("⚠" if value > 0 else "OK"), str(value))
        elif key in df.columns:
            count = int(df[key].sum())
            status, count_display = ("⚠" if count > 0 else "OK"), str(count)
        else:
            status, count_display = "N/A", "—"
        summary_rows.append([str(number), ctrl_title, status, count_display])
    _docx_add_table(doc, summary_rows, col_widths_cm=[1, 8, 2, 3])
    doc.add_paragraph()

    doc.add_heading(DUMP_COMPLETENESS_HEADER, level=2)
    doc.add_paragraph(DUMP_COMPLETENESS_GUIDANCE)
    dump_rows = [["Field", "Status"]]
    for label, candidates in DUMP_COMPLETENESS_COLUMNS:
        present = any(c in df.columns and df[c].notna().any() for c in candidates)
        dump_rows.append([label, "OK" if present else "NOK"])
    _docx_add_table(doc, dump_rows)
    doc.add_paragraph()

    for number, ctrl_title, guidance, key in CONTROL_SUBSECTIONS:
        doc.add_heading(f"{number}.{ctrl_title}", level=2)
        if guidance:
            doc.add_paragraph(guidance)
        count, note, subset = None, None, None
        if key is None:
            note = "N/A — nécessite une configuration propre à l'entreprise, non déductible des seules données ingérées."
        elif key == "_active_count":
            if "account_status" in df.columns:
                subset = df[df["account_status"].apply(_is_active_account)]
                count = len(subset)
            else:
                note = "N/A — colonne 'account_status' absente."
        elif key == "_deleted":
            # Un compte supprimé n'existe plus dans le fichier ACTUEL —
            # uniquement retrouvable dans la revue précédente.
            value = comparison_stats.get("deleted")
            if value is None:
                note = "N/A — aucune revue précédente fournie pour établir la comparaison."
            else:
                count = value
                names = comparison_stats.get("deleted_accounts") or []
                if names and previous_df is not None and "username" in previous_df.columns:
                    subset = previous_df[previous_df["username"].astype(str).isin(names)]
        elif key in ("_created", "_reactivated", "_profile_modified"):
            stat_key = key.lstrip("_")
            value = comparison_stats.get(stat_key)
            if value is None:
                note = "N/A — aucune revue précédente fournie pour établir la comparaison."
            else:
                count = value
                names = comparison_stats.get(f"{stat_key}_accounts") or []
                if names and "username" in df.columns:
                    subset = df[df["username"].astype(str).isin(names)]
        elif key in df.columns:
            subset = df[df[key] == True]  # noqa: E712
            count = len(subset)
        else:
            note = f"N/A — colonne '{key}' absente des données ingérées."

        if count is not None:
            p = doc.add_paragraph()
            p.add_run(f"{count} compte(s) concerné(s).").bold = True
            if subset is not None and len(subset):
                default_cols = ["username", "full_name", "system", "review_action"]
                cols = [c for c in (CONTROL_TABLE_COLUMNS.get(key) or default_cols) if c in subset.columns]
                if cols:
                    # Pas de plafond ici : ce sont les 18 sections qui sont
                    # effectivement revues, la complétude prime sur la
                    # longueur du document.
                    display = subset[cols].fillna("").astype(str)
                    detail_rows = [[ALL_COLUMN_LABELS.get(c, c) for c in cols]] + display.values.tolist()
                    _docx_add_table(doc, detail_rows)
        else:
            doc.add_paragraph(note)

    # ---- V. CONCLUSION ----
    doc.add_heading(CONCLUSION_HEADING, level=1)
    total_accounts = len(df)
    critical_count = int((df["risk_level"] == "Critique").sum()) if "risk_level" in df.columns else 0
    elevated_count = int((df["risk_level"] == "Élevé").sum()) if "risk_level" in df.columns else 0
    if total_accounts == 0:
        conclusion_text = "No account was included in the scope of this review cycle."
    elif critical_count == 0 and elevated_count == 0:
        conclusion_text = (
            f"Out of {total_accounts} account(s) reviewed, none were classified as Critical or "
            f"High risk on this cycle. No immediate corrective action is required beyond the "
            f"standard follow-up of any Medium-risk items listed above."
        )
    else:
        conclusion_text = (
            f"Out of {total_accounts} account(s) reviewed, {critical_count} were classified as "
            f"Critical risk and {elevated_count} as High risk. Corrective actions are detailed in "
            f"the exceptions report above and must be tracked to closure before the next review cycle."
        )
    doc.add_paragraph(conclusion_text)

    # ---- Annexe opérationnelle ----
    doc.add_heading("Annexe opérationnelle — Détail exploitable du cycle", level=1)

    quality_report = compute_data_quality_report(df)
    doc.add_heading(f"Qualité des données — fiabilité estimée {quality_report['reliability_pct']}%", level=2)
    doc.add_paragraph(
        "Vérification préalable de la fiabilité du fichier source, avant les contrôles IAM "
        "eux-mêmes — purement informatif, ne modifie aucune donnée ni aucun résultat d'analyse."
    )
    issue_labels = {
        "username_missing": "Identifiants de compte manquants",
        "duplicate_usernames": "Comptes en doublon (même identifiant + système)",
        "invalid_dates": "Dates de dernière connexion non interprétables",
        "unknown_status": "Statuts de compte non reconnus",
        "system_missing": "Système non renseigné",
        "manager_missing": "Manager non renseigné",
    }
    quality_rows = [["Indicateur", "Valeur"], ["Lignes analysées", str(quality_report["total_rows"])]]
    for key, label in issue_labels.items():
        count = quality_report["issues"].get(key)
        if count:
            quality_rows.append([label, str(count)])
    if len(quality_rows) > 2:
        _docx_add_table(doc, quality_rows)
    else:
        doc.add_paragraph("Aucun problème de qualité détecté sur ce fichier.")
    doc.add_paragraph()

    doc.add_heading("Résumé exécutif", level=2)
    risk_counts = df["risk_level"].value_counts() if "risk_level" in df.columns else {}
    exec_rows = [["Indicateur", "Valeur"], ["Total comptes analysés", str(len(df))]]
    for risk in RISK_COLORS_HEX:
        exec_rows.append([risk, str(int(risk_counts.get(risk, 0)))])
    if "is_terminated_but_active" in df.columns:
        exec_rows.append(["Comptes actifs d'employés partis", str(int(df["is_terminated_but_active"].sum()))])
    if "is_dormant" in df.columns:
        exec_rows.append(["Comptes dormants", str(int(df["is_dormant"].sum()))])
    if "is_never_used" in df.columns:
        exec_rows.append(["Comptes jamais utilisés", str(int(df["is_never_used"].sum()))])
    if "is_password_stale" in df.columns:
        exec_rows.append(["Mots de passe périmés", str(int(df["is_password_stale"].sum()))])
    if "is_duplicate_account" in df.columns:
        exec_rows.append(["Comptes en doublon", str(int(df["is_duplicate_account"].sum()))])
    if "is_locked" in df.columns:
        exec_rows.append(["Comptes verrouillés (hors dormance)", str(int(df["is_locked"].sum()))])
    _docx_add_table(doc, exec_rows)
    doc.add_paragraph()

    doc.add_heading("Actions prioritaires", level=2)
    if "risk_level" in df.columns:
        priority_df = df[df["risk_level"].isin(["Critique", "Élevé"])]
        cols = [c for c, _ in DISPLAY_COLUMNS if c in priority_df.columns]
        labels = dict(DISPLAY_COLUMNS)
        if len(priority_df) and cols:
            display = priority_df[cols].fillna("").astype(str)
            rows = [[labels[c] for c in cols]] + display.values.tolist()
            _docx_add_table(doc, rows)
        else:
            doc.add_paragraph("Aucun compte en risque Critique ou Élevé sur ce cycle.")
    doc.add_paragraph()

    if "risk_score" in df.columns and "risk_score_reasons" in df.columns and len(df):
        top_scored = df[df["risk_score"] > 0].sort_values("risk_score", ascending=False).head(10)
        if len(top_scored):
            doc.add_heading("Score de risque — détail du calcul (10 comptes les plus exposés)", level=2)
            doc.add_paragraph(
                "Score additif 0-100, plafonné, calculé à partir des signaux détectés pour chaque "
                "compte — pour comprendre POURQUOI un compte atteint un score donné, pas seulement "
                "l'afficher."
            )
            for _, row in top_scored.iterrows():
                uname = row.get("username", "?")
                p = doc.add_paragraph()
                p.add_run(f"{uname} — Score : {int(row['risk_score'])}/100").bold = True
                reasons_text = " · ".join(f"{label} (+{pts})" for label, pts in row["risk_score_reasons"])
                doc.add_paragraph(reasons_text)
    doc.add_paragraph()

    # ---- Rapport des exceptions (narratif, format audit classique) ----
    doc.add_heading("Rapport des exceptions", level=2)
    if "system" not in df.columns or "review_action" not in df.columns:
        doc.add_paragraph(
            "Champs insuffisants pour générer le rapport des exceptions "
            "(système et action recommandée requis)."
        )
    else:
        flagged = df[df["review_action"] != "Aucune action"]
        if flagged.empty:
            doc.add_paragraph("Aucune exception à signaler sur ce cycle.")
        else:
            counter = 1
            for system_name, system_group in flagged.groupby("system"):
                for action, action_group in system_group.groupby("review_action"):
                    count = len(action_group)
                    p = doc.add_paragraph()
                    p.add_run(f"Exception {counter} — {system_name} : ").bold = True
                    p.add_run(f"{count} compte(s) avec le statut « {action} ».")
                    narrative = ACTION_NARRATIVE.get(
                        action, "Voir la sous-section de contrôle correspondante ci-dessus pour le détail nominatif."
                    )
                    doc.add_paragraph(narrative)
                    counter += 1
    doc.add_paragraph()

    doc.add_heading("Validation", level=2)
    placeholder = "[À compléter]"
    _docx_add_table(doc, [
        ["Rôle", "Nom", "Date"],
        ["Préparé par", prepared_by or placeholder, datetime.now().strftime("%d/%m/%Y")],
        ["Revu par", reviewed_by or placeholder, ""],
        ["Approuvé par", approved_by or placeholder, ""],
    ])

    doc.save(str(output_path))
    logger.info(f"Rapport Word généré : {output_path}")
    return output_path


def generate_pdf_report(
    df: pd.DataFrame,
    output_path: str | Path,
    period: str | None = None,
    dormant_threshold_days: int = 90,
    prepared_by: str | None = None,
    reviewed_by: str | None = None,
    approved_by: str | None = None,
    department: str | None = None,
    editor: str | None = None,
    application_scope: str | None = None,
    document_version: str = "1.0",
    include_controls_reference: bool = True,
    previous_df: pd.DataFrame | None = None,
    logo_path: str | Path | None = None,
) -> Path:
    """
    Génère un rapport PDF de revue d'accès structuré et réutilisable d'un
    cycle à l'autre : résumé exécutif, méthodologie, actions prioritaires,
    puis détail système par système — plutôt qu'un unique tableau brut.

    `period` est un libellé libre (ex. "T1 2026", "Mars 2026") affiché en
    en-tête du rapport ; par défaut, le trimestre courant est déduit
    automatiquement, pour que la fonction reste utilisable telle quelle à
    chaque exécution sans argument supplémentaire.

    `prepared_by`, `reviewed_by`, `approved_by` : noms affichés dans le
    tableau de validation en fin de rapport. Laissés vides, ils affichent
    un espace à remplir à la main plutôt que de faire échouer la génération.

    `department`, `editor`, `application_scope`, `document_version` :
    bloc d'en-tête officiel du document (département émetteur, rédacteur,
    périmètre applicatif, version) — entièrement configurables à chaque
    génération, jamais figés dans le code, pour que ce rapport reste
    l'outil officiel de l'équipe plutôt qu'un document lié à une personne
    ou une entreprise en particulier.

    `include_controls_reference` : conservé pour compatibilité ; les 18
    contrôles (section I) et les principes de création (section II) font
    désormais partie intégrante du document officiel et sont toujours
    inclus, quelle que soit la valeur de ce paramètre.

    `previous_df` : DataFrame de la revue précédente (même format que
    `df`, déjà passé par analyze_access), pour calculer une vraie
    comparaison chiffrée entre les deux cycles (comptes créés, supprimés,
    réactivés, profils modifiés) en section "III.a Summary of the
    review". Laissé à None, cette sous-section n'affiche que le cycle
    courant, sans comparaison.

    `logo_path` : chemin vers un fichier image (PNG/JPG) à afficher en
    en-tête du document. Cet outil ne fournit et ne recrée aucun logo
    d'entreprise — laissé à None (par défaut), l'en-tête reste sans logo.
    Fournissez le fichier réel de votre entreprise pour l'inclure.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    period_label = period or _current_quarter_label()

    doc = SimpleDocTemplate(
        str(output_path), pagesize=landscape(A4),
        topMargin=1.5 * cm, bottomMargin=1.5 * cm, leftMargin=1.5 * cm, rightMargin=1.5 * cm,
    )
    available_width = doc.pagesize[0] - doc.leftMargin - doc.rightMargin
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("TitleCustom", parent=styles["Title"], fontSize=18, spaceAfter=4, fontName=DEFAULT_FONT_BOLD)
    subtitle_style = ParagraphStyle("Subtitle", parent=styles["Normal"], fontSize=10, textColor=colors.grey, fontName=DEFAULT_FONT)
    section_style = ParagraphStyle("SectionH", parent=styles["Heading2"], spaceBefore=14, spaceAfter=6, fontName=DEFAULT_FONT_BOLD)
    system_style = ParagraphStyle(
        "SystemH", parent=styles["Heading3"], textColor=colors.HexColor("#1F2937"),
        spaceBefore=12, spaceAfter=4, fontName=DEFAULT_FONT_BOLD,
    )
    note_style = ParagraphStyle("Note", parent=styles["Normal"], fontSize=8.5, textColor=colors.grey, spaceAfter=10, fontName=DEFAULT_FONT)
    exception_style = ParagraphStyle(
        "Exception", parent=styles["Normal"], fontSize=9.5, spaceBefore=8, spaceAfter=2, fontName=DEFAULT_FONT,
    )
    action_style = ParagraphStyle(
        "ActionText", parent=styles["Normal"], fontSize=9, textColor=colors.HexColor("#374151"), spaceAfter=4, fontName=DEFAULT_FONT,
    )

    elements = []

    # ---- Logo (optionnel — jamais fourni par l'outil lui-même) ----
    if logo_path:
        logo_path = Path(logo_path)
        if logo_path.exists():
            try:
                from reportlab.platypus import Image as RLImage
                logo_img = RLImage(str(logo_path))
                # Limite raisonnable de hauteur pour ne pas déséquilibrer l'en-tête,
                # en conservant les proportions réelles de l'image fournie.
                max_height = 1.8 * cm
                if logo_img.imageHeight > 0:
                    ratio = logo_img.imageWidth / logo_img.imageHeight
                    logo_img.drawHeight = max_height
                    logo_img.drawWidth = max_height * ratio
                elements.append(logo_img)
                elements.append(Spacer(1, 0.3 * cm))
            except Exception as e:
                logger.warning(f"Logo non inséré ({logo_path}) : {e}")
        else:
            logger.warning(f"Chemin de logo introuvable, en-tête généré sans logo : {logo_path}")

    # ---- En-tête officiel (structure fidèle au template) ----
    header_data = [
        [department or "TECHNOLOGY DEPARTMENT", "Editor: " + (editor or "[FULL NAME]")],
        ["REVIEW OF THE APPLICATION ACCOUNTS", f"Version {document_version}"],
        ["Scope: " + (application_scope or "[Application Name]"), "ISM"],
        ["", period_label],
    ]
    header_table = Table(header_data, colWidths=[available_width * 0.6, available_width * 0.4])
    header_table.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#D9D9D9")),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    elements.append(header_table)
    elements.append(Spacer(1, 0.6 * cm))

    elements.append(Paragraph("APPLICATION ACCOUNTS REVIEW", title_style))
    elements.append(Spacer(1, 0.3 * cm))
    elements.append(Paragraph("Baseline evidence of the review", section_style))
    elements.append(Paragraph("Data source: Email or automated reception", note_style))
    elements.append(Paragraph(f"Date of extraction: {datetime.now().strftime('%d/%m/%Y')}", note_style))
    elements.append(Paragraph(f"Review date: {datetime.now().strftime('%d/%m/%Y')}", note_style))
    elements.append(Spacer(1, 0.4 * cm))

    version_data = [
        ["Version", "Created / Edited", "By", "Comment"],
        [document_version, datetime.now().strftime("%d/%m/%Y"), editor or "[SYSTEM OWNER FULL NAME]", "N/A"],
    ]
    version_table = Table(version_data, colWidths=[available_width * w for w in (0.12, 0.2, 0.44, 0.24)])
    version_table.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#D9D9D9")),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F0F0F0")),
        ("FONTNAME", (0, 0), (-1, -1), DEFAULT_FONT),
        ("FONTNAME", (0, 0), (-1, 0), DEFAULT_FONT_BOLD),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    elements.append(version_table)
    elements.append(Spacer(1, 0.4 * cm))

    distribution_data = [
        ["Distribution", "Department/Role", "Action", "Information"],
        [department or "OWNER DEPARTMENT", "SYSTEM OWNER ROLE", "X", "X"],
        ["ENTERPRISE INFORMATION SECURITY", "ENTERPRISE INFORMATION SECURITY", "", "X"],
    ]
    distribution_table = Table(distribution_data, colWidths=[available_width * w for w in (0.32, 0.32, 0.18, 0.18)])
    distribution_table.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#D9D9D9")),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F0F0F0")),
        ("FONTNAME", (0, 0), (-1, -1), DEFAULT_FONT),
        ("FONTNAME", (0, 0), (-1, 0), DEFAULT_FONT_BOLD),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("ALIGN", (2, 0), (3, -1), "CENTER"),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    elements.append(distribution_table)
    elements.append(Spacer(1, 0.5 * cm))

    # ---- VALIDATION ----
    elements.append(Paragraph("VALIDATION", section_style))
    placeholder = "[TO BE COMPLETED]"
    val_data = [
        ["SYSTEM OWNER:", "MANAGER:", "SENIOR MANAGER:"],
        [prepared_by or placeholder, reviewed_by or placeholder, approved_by or placeholder],
        ["[SIGNATURE - DATE]", "[SIGNATURE - DATE]", "[SIGNATURE - DATE]"],
    ]
    val_table = Table(val_data, colWidths=[available_width / 3] * 3)
    val_table.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#D9D9D9")),
        ("FONTNAME", (0, 0), (-1, -1), DEFAULT_FONT),
        ("FONTNAME", (0, 0), (-1, 0), DEFAULT_FONT_BOLD),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))
    elements.append(val_table)
    elements.append(Spacer(1, 0.2 * cm))
    ctio_table = Table(
        [["CTIO:"], ["[FULL NAME]"], ["[SIGNATURE - DATE]"]],
        colWidths=[available_width],
    )
    ctio_table.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#D9D9D9")),
        ("FONTNAME", (0, 0), (0, 0), DEFAULT_FONT_BOLD),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))
    elements.append(ctio_table)
    elements.append(Spacer(1, 0.6 * cm))

    # ---- I. OBJECTIF ----
    elements.append(Paragraph("I. OBJECTIF", section_style))
    elements.append(Paragraph(OBJECTIVE_INTRO, note_style))
    for b in OBJECTIVE_BULLETS:
        elements.append(Paragraph(f"•&nbsp;&nbsp;{b}", note_style))
    elements.append(Paragraph(OBJECTIVE_CONTROL_INTRO, note_style))
    elements.append(Spacer(1, 0.2 * cm))

    controls_data = [["SN", "Control", "Control Description/Expectations"]]
    controls_cell_style = ParagraphStyle("ControlsCell", fontSize=8, leading=10.5, fontName=DEFAULT_FONT)
    for sn, name, desc in TEMPLATE_CONTROLS:
        controls_data.append([
            str(sn),
            Paragraph(name, controls_cell_style),
            Paragraph(desc.replace("\n", "<br/>"), controls_cell_style),
        ])
    controls_table = Table(
        controls_data,
        colWidths=[available_width * w for w in (0.05, 0.20, 0.75)],
        repeatRows=1,
    )
    controls_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1F2937")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, -1), DEFAULT_FONT),
        ("FONTNAME", (0, 0), (-1, 0), DEFAULT_FONT_BOLD),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#D9D9D9")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F9F9F9")]),
    ]))
    elements.append(controls_table)
    elements.append(Spacer(1, 0.5 * cm))

    # ---- II. PRINCIPLES OF APPLICATION ACCOUNT CREATION ----
    elements.append(Paragraph("II. PRINCIPLES OF APPLICATION ACCOUNT CREATION", section_style))
    elements.append(Paragraph(PRINCIPLES_INTRO, note_style))
    elements.append(Spacer(1, 0.15 * cm))
    elements.append(Paragraph("1. Types of Accounts Created", system_style))
    for label, desc in ACCOUNT_TYPES:
        elements.append(Paragraph(f"<b>{label}</b>{desc}", note_style))
    elements.append(Spacer(1, 0.15 * cm))
    elements.append(Paragraph("2. Account Creation Process", system_style))
    elements.append(Paragraph(CREATION_PROCESS_INTRO, note_style))
    for label, desc in CREATION_PROCESS_ITEMS:
        elements.append(Paragraph(f"<b>{label}</b>{desc}", note_style))
    elements.append(Spacer(1, 0.6 * cm))

    # ==================================================================
    # À partir d'ici : contenu généré dynamiquement à partir des données
    # réellement ingérées (III. REVIEW DETAILS et suite) — pas une
    # reproduction du template, mais son application concrète aux
    # données de ce cycle de revue.
    # ==================================================================
    elements.append(Paragraph("III. REVIEW DETAILS", section_style))

    if "_ocr_source" in df.columns and df["_ocr_source"].any():
        ocr_count = int(df["_ocr_source"].sum())
        ocr_warning_style = ParagraphStyle(
            "OcrWarning", parent=styles["Normal"], fontSize=9.5,
            textColor=colors.HexColor("#A13D2E"), fontName=DEFAULT_FONT_BOLD,
            backColor=colors.HexColor("#FBEAE7"), borderPadding=8, spaceAfter=10,
        )
        elements.append(Paragraph(
            f"⚠ AVERTISSEMENT : {ocr_count} compte(s) de ce rapport proviennent d'une "
            f"reconnaissance optique de caractères (OCR) sur image, pas d'un fichier "
            f"structuré. L'OCR peut introduire des erreurs de lecture (ex. '1' lu comme "
            f"'l', '0' lu comme 'O'). Ces comptes doivent être vérifiés manuellement "
            f"avant toute décision — ne pas leur accorder la même confiance qu'aux autres.",
            ocr_warning_style,
        ))

    elements.append(Paragraph(
        f"Méthodologie : un compte est considéré « dormant » sans connexion depuis plus de "
        f"{dormant_threshold_days} jours — y compris un compte n'ayant jamais enregistré la "
        f"moindre connexion depuis sa création. Chaque compte reçoit un niveau de risque et une "
        f"action recommandée selon son statut (compte actif d'un employé parti, compte "
        f"privilégié dormant, absence de manager identifié). Cet outil ne recommande jamais la "
        f"suppression d'un compte, uniquement sa désactivation — réversible, et applicable sans "
        f"historique préalable.",
        note_style,
    ))

    # ---- III.a Summary of the review (comparaison avec la revue précédente) ----
    comparison_elements, comparison_stats = _build_review_comparison_section(
        df, previous_df, section_style, note_style, available_width,
    )
    elements.extend(comparison_elements)
    elements.append(Spacer(1, 0.4 * cm))

    # ---- IV. ACCOUNT DETAILS BY CONTROL (18 sous-sections fidèles au template) ----
    elements.append(Paragraph("IV. ACCOUNT DETAILS BY CONTROL", section_style))
    elements.append(Paragraph(SECTION_IV_INTRO, note_style))
    elements.append(Paragraph(
        "Note : la colonne « Action recommandée » reflète toujours l'action prioritaire globale "
        "du compte (tous contrôles confondus), pas nécessairement la raison précise de sa présence "
        "dans la sous-section en cours — un compte peut apparaître dans plusieurs sections à la fois.",
        note_style,
    ))

    # Vue d'ensemble compacte avant le détail verbeux — lecture en un
    # coup d'œil de l'état des 18 contrôles, avant d'entrer dans le détail.
    elements.append(Paragraph("Control Summary", system_style))
    elements.append(_build_control_summary_table(df, comparison_stats, available_width))
    elements.append(Spacer(1, 0.4 * cm))

    elements.append(Paragraph(DUMP_COMPLETENESS_HEADER, system_style))
    elements.append(Paragraph(DUMP_COMPLETENESS_GUIDANCE, note_style))
    elements.append(_build_dump_completeness_table(df, available_width))
    elements.append(Spacer(1, 0.3 * cm))
    elements.extend(_build_control_subsections(
        df, comparison_stats, section_style, system_style, note_style, action_style, available_width,
        previous_df=previous_df,
    ))

    # ---- V. CONCLUSION ----
    elements.append(Paragraph(CONCLUSION_HEADING, section_style))
    total_accounts = len(df)
    critical_count = int((df["risk_level"] == "Critique").sum()) if "risk_level" in df.columns else 0
    elevated_count = int((df["risk_level"] == "Élevé").sum()) if "risk_level" in df.columns else 0
    if total_accounts == 0:
        conclusion_text = "No account was included in the scope of this review cycle."
    elif critical_count == 0 and elevated_count == 0:
        conclusion_text = (
            f"Out of {total_accounts} account(s) reviewed, none were classified as Critical or "
            f"High risk on this cycle. No immediate corrective action is required beyond the "
            f"standard follow-up of any Medium-risk items listed above."
        )
    else:
        conclusion_text = (
            f"Out of {total_accounts} account(s) reviewed, {critical_count} were classified as "
            f"Critical risk and {elevated_count} as High risk. Corrective actions are detailed in "
            f"the exceptions report above and must be tracked to closure before the next review cycle."
        )
    elements.append(Paragraph(conclusion_text, note_style))
    elements.append(Spacer(1, 0.5 * cm))

    # ==================================================================
    # Au-delà du template officiel : contenu opérationnel supplémentaire,
    # généré à partir des données réelles pour faciliter le traitement
    # concret des exceptions — pas une section du document original.
    # ==================================================================
    elements.append(Paragraph("Annexe opérationnelle — Détail exploitable du cycle", section_style))

    # ---- Qualité des données (contrôle préalable, informatif) ----
    quality_report = compute_data_quality_report(df)
    elements.append(Paragraph(
        f"Qualité des données — fiabilité estimée {quality_report['reliability_pct']}%",
        system_style,
    ))
    elements.append(Paragraph(
        "Vérification préalable de la fiabilité du fichier source, avant les contrôles IAM "
        "eux-mêmes — purement informatif, ne modifie aucune donnée ni aucun résultat d'analyse.",
        note_style,
    ))
    issue_labels = {
        "username_missing": "Identifiants de compte manquants",
        "duplicate_usernames": "Comptes en doublon (même identifiant + système)",
        "invalid_dates": "Dates de dernière connexion non interprétables",
        "unknown_status": "Statuts de compte non reconnus",
        "system_missing": "Système non renseigné",
        "manager_missing": "Manager non renseigné",
    }
    quality_rows = [["Indicateur", "Valeur"], ["Lignes analysées", str(quality_report["total_rows"])]]
    for key, label in issue_labels.items():
        count = quality_report["issues"].get(key)
        if count:
            quality_rows.append([label, str(count)])
    if len(quality_rows) > 2:
        quality_table = Table(quality_rows, colWidths=[available_width * 0.7, available_width * 0.3])
        quality_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1F2937")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, -1), DEFAULT_FONT),
            ("FONTNAME", (0, 0), (-1, 0), DEFAULT_FONT_BOLD),
            ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#D9D9D9")),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ]))
        elements.append(quality_table)
    else:
        elements.append(Paragraph("Aucun problème de qualité détecté sur ce fichier.", note_style))
    elements.append(Spacer(1, 0.4 * cm))

    # ---- Résumé exécutif ----
    elements.append(Paragraph("Résumé exécutif", section_style))
    risk_counts = df["risk_level"].value_counts() if "risk_level" in df.columns else {}
    summary_data = [["Indicateur", "Valeur"], ["Total comptes analysés", str(len(df))]]
    for risk in RISK_COLORS_HEX:
        summary_data.append([risk, str(int(risk_counts.get(risk, 0)))])
    if "is_terminated_but_active" in df.columns:
        summary_data.append(["Comptes actifs d'employés partis", str(int(df["is_terminated_but_active"].sum()))])
    if "is_dormant" in df.columns:
        summary_data.append(["Comptes dormants", str(int(df["is_dormant"].sum()))])
    if "is_never_used" in df.columns:
        summary_data.append(["Comptes jamais utilisés", str(int(df["is_never_used"].sum()))])
    if "is_password_stale" in df.columns:
        summary_data.append(["Mots de passe périmés", str(int(df["is_password_stale"].sum()))])
    if "is_privileged_flag" in df.columns and "has_non_expiring_password" in df.columns:
        summary_data.append([
            "Comptes privilégiés à mot de passe n'expirant jamais",
            str(int((df["is_privileged_flag"] & df["has_non_expiring_password"]).sum())),
        ])
    if "is_duplicate_account" in df.columns:
        summary_data.append(["Comptes en doublon", str(int(df["is_duplicate_account"].sum()))])
    if "is_locked" in df.columns:
        summary_data.append(["Comptes verrouillés (hors dormance)", str(int(df["is_locked"].sum()))])

    summary_table = Table(summary_data, colWidths=[9 * cm, 4 * cm])
    summary_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1F2937")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, -1), DEFAULT_FONT),
        ("FONTNAME", (0, 0), (-1, 0), DEFAULT_FONT_BOLD),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#D9D9D9")),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F5F5F5")]),
    ]))
    elements.append(summary_table)

    export_df_full = _prepare_export_df(df)

    # ---- Actions prioritaires (Critique + Élevé, tous systèmes confondus) ----
    if "Risque" in export_df_full.columns:
        priority_df = export_df_full[export_df_full["Risque"].isin(["Critique", "Élevé"])]
        elements.append(Paragraph(
            f"Actions prioritaires ({len(priority_df)} compte(s) à traiter en premier)",
            section_style,
        ))
        if len(priority_df):
            elements.append(_risk_styled_table(priority_df, available_width))
        else:
            elements.append(Paragraph("Aucun compte en risque Critique ou Élevé sur ce cycle.", styles["Normal"]))

    # ---- Score de risque explicable : détail du calcul pour les comptes
    # les plus exposés — traçabilité d'audit ("pourquoi ce score ?"),
    # plutôt qu'une étiquette de risque sans justification. ----
    if "risk_score" in df.columns and "risk_score_reasons" in df.columns and len(df):
        top_scored = df[df["risk_score"] > 0].sort_values("risk_score", ascending=False).head(10)
        if len(top_scored):
            elements.append(Paragraph("Score de risque — détail du calcul (10 comptes les plus exposés)", section_style))
            elements.append(Paragraph(
                "Score additif 0-100, plafonné, calculé à partir des signaux détectés pour chaque "
                "compte — pour comprendre POURQUOI un compte atteint un score donné, pas seulement "
                "l'afficher.",
                note_style,
            ))
            for _, row in top_scored.iterrows():
                uname = row.get("username", "?")
                reasons_text = " · ".join(f"{label} (+{pts})" for label, pts in row["risk_score_reasons"])
                elements.append(Paragraph(
                    f"<b>{uname}</b> — Score : {row['risk_score']}/100", action_style,
                ))
                elements.append(Paragraph(reasons_text, note_style))
            elements.append(Spacer(1, 0.3 * cm))

    # ---- Rapport des exceptions (narratif, format audit classique) ----
    elements.extend(_build_exceptions_section(df, section_style, exception_style, action_style))

    # ---- Sign-off (validation) ----
    elements.append(Paragraph("Validation", section_style))
    placeholder = "[À compléter]"
    signoff_data = [
        ["Rôle", "Nom", "Date"],
        ["Préparé par", prepared_by or placeholder, datetime.now().strftime("%d/%m/%Y")],
        ["Revu par", reviewed_by or placeholder, ""],
        ["Approuvé par", approved_by or placeholder, ""],
    ]
    signoff_table = Table(signoff_data, colWidths=[5 * cm, 8 * cm, 4 * cm])
    signoff_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1F2937")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, -1), DEFAULT_FONT),
        ("FONTNAME", (0, 0), (-1, 0), DEFAULT_FONT_BOLD),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#D9D9D9")),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    elements.append(signoff_table)

    doc.build(elements)
    logger.info(f"Rapport PDF généré ({period_label}) : {output_path}")
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
