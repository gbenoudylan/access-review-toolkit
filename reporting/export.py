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
from analysis.risk_acceptance import get_accepted_findings_detail
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
# Même palette, indexée sur les valeurs ANGLAISES traduites — pour les
# points du code qui colorent une cellule après que _prepare_export_df
# a déjà traduit la colonne Risk en anglais (le dict français ne
# correspondrait alors plus à rien).

# Formulation générique associée à chaque action recommandée, pour la
# section narrative "Rapport des exceptions" — inspirée des standards du
# secteur (revue trimestrielle des accès), jamais copiée d'un document
# précis : ces recommandations sont volontairement génériques pour rester
# valables quelle que soit l'entreprise ou le système concerné.
DISPLAY_COLUMNS = [
    ("username", "Account"),
    ("user_id", "Employee ID"),
    ("full_name", "Name"),
    ("department", "Department"),
    ("system", "System"),
    ("manager", "Manager"),
    ("account_status", "Account Status"),
    ("employee_status", "HR Status"),
    ("days_since_last_login", "Days Since Last Login"),
    ("days_since_password_change", "Days Since Password Change"),
    ("is_privileged_flag", "Privileged"),
    ("has_non_expiring_password", "Password Never Expires"),
    ("review_action", "Recommended Action"),
    ("risk_score", "Score"),
    ("risk_level", "Risk"),
]

# Libellés pour les colonnes justificatives des tableaux par contrôle,
# non couvertes par DISPLAY_COLUMNS (valeurs brutes plutôt que calculées).
_EXTRA_COLUMN_LABELS = {
    "last_login_date": "Last Login (raw)",
    "account_created_date": "Creation Date",
    "password_last_set": "Last Password Change (raw)",
    "role": "Role",
    "password_status": "Password Status",
}
ALL_COLUMN_LABELS = {**dict(DISPLAY_COLUMNS), **_EXTRA_COLUMN_LABELS}

# Table de traduction des VALEURS internes (françaises, utilisées partout
# dans analysis/access_review.py, le dashboard, et les tests existants —
# volontairement non modifiées à la source pour ne rien casser) vers
# l'anglais, appliquée UNIQUEMENT à la frontière du rendu des rapports
# (Excel/Word/PDF). Le dashboard interactif et le code d'analyse restent
# donc inchangés ; seuls les documents générés changent de langue.
_VALUE_TRANSLATIONS = {
    # Niveaux de risque
    "Critique": "Critical", "Élevé": "High", "Moyen": "Medium", "Faible": "Low",
    # Actions recommandées (review_action)
    "Aucune action": "No action",
    "Désactiver (dormant)": "Disable (dormant)",
    "Désactiver (jamais utilisé)": "Disable (never used)",
    "Désactiver (privilégié dormant)": "Disable (dormant privileged)",
    "Exiger un changement de mot de passe": "Require password change",
    "Forcer l'expiration du mot de passe (privilégié)": "Force password expiration (privileged)",
    "Fusionner les doublons (ne garder qu'un compte actif)": "Merge duplicates (keep only one active account)",
    "Identifier un owner": "Identify an owner",
    "Nettoyer (compte verrouillé)": "Clean up (locked account)",
    "Renommer selon la convention": "Rename according to naming convention",
    "Révoquer immédiatement": "Revoke immediately",
    "Vérifier (compte de test présumé)": "Verify (presumed test account)",
    "Vérifier (compte générique/orphelin présumé)": "Verify (presumed generic/orphaned account)",
    "Vérifier (date de dernière connexion non exploitable)": "Verify (last login date not usable)",
    "Vérifier avec le propriétaire technique (compte de service)": "Verify with technical owner (service account)",
    "Vérifier avec le propriétaire technique (mot de passe, compte de service)": "Verify with technical owner (password, service account)",
    # Autres valeurs pouvant apparaître telles quelles dans les tableaux
    "Inconnu (non vérifiable)": "Unknown (not verifiable)",
    True: "Yes", False: "No",
    # Libellés du détail explicable du score de risque (risk_score_reasons,
    # analysis/access_review.py) — rendus tels quels dans le PDF/Word sans
    # passer par _prepare_export_df, traduits séparément via _translate_value
    # au moment de construire le texte des raisons.
    "Employé parti, compte encore actif": "Departed employee, account still active",
    "Compte dormant ou jamais utilisé": "Dormant or never used account",
    "Compte privilégié": "Privileged account",
    "Mot de passe n'expirant jamais (privilégié)": "Password never expires (privileged)",
    "Dernier changement de mot de passe inconnu (non vérifiable)": "Last password change unknown (not verifiable)",
    "Mot de passe périmé (> seuil retenu)": "Stale password (> retained threshold)",
    "Date de dernière connexion non exploitable (format tronqué)": "Last login date not usable (truncated format)",
    "Aucun manager/owner identifié": "No manager/owner identified",
    "Compte en doublon": "Duplicate account",
    "Compte verrouillé": "Locked account",
    "Nom évoquant un compte de test": "Name suggests a test account",
    "Nom non conforme à la convention": "Name does not follow naming convention",
}


def _translate_value(value):
    """Traduit une valeur de donnée interne (française) vers l'anglais
    pour l'affichage dans les rapports générés, sans toucher à la donnée
    d'origine — un simple lookup, la valeur est rendue telle quelle si
    elle n'est pas dans la table (ex. un nom de compte, une date)."""
    return _VALUE_TRANSLATIONS.get(value, value)


RISK_COLORS_HEX_EN = {_translate_value(k): v for k, v in RISK_COLORS_HEX.items()}

# Colonnes justificatives par contrôle : celles qui permettent de VÉRIFIER
# pourquoi un compte est listé, pas seulement l'action qui en résulte —
# ex. pour "Dormant", voir la dernière connexion réelle et son ancienneté
# en jours, pas seulement l'action "Désactiver". Clé = même clé que
# CONTROL_SUBSECTIONS (booléenne, ou "_created"/"_deleted"/...).
CONTROL_TABLE_COLUMNS = {
    "is_dormant": ["username", "full_name", "system", "account_status", "last_login_date", "days_since_last_login", "review_action"],
    "is_test_account": ["username", "full_name", "system", "account_status", "review_action"],
    "is_orphaned_account": ["username", "full_name", "system", "account_status", "review_action"],
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

    # Une valeur manquante dans 'Days Since Password Change' finirait sinon
    # en case vide (.fillna("") générique plus bas) — une ligne signalée
    # comme mot de passe périmé SANS AUCUNE justification visible dans le
    # tableau, alors que la raison même du signalement est justement
    # l'absence de date exploitable. Rendu explicite pour rester honnête
    # sur ce qu'on sait (une date ancienne connue) vs ce qu'on ne sait
    # pas (aucune date exploitable) — les deux comptent comme signal de
    # risque, mais ne doivent pas se ressembler dans le rapport.
    if "Days Since Password Change" in export_df.columns:
        export_df["Days Since Password Change"] = export_df["Days Since Password Change"].apply(
            lambda v: "Unknown (not verifiable)" if pd.isna(v) else v
        )

    # Tri sur les valeurs FRANÇAISES internes (risk_level n'est traduit
    # qu'après, colonne par colonne, pour ne pas casser ce lookup).
    risk_order = {"Critique": 0, "Élevé": 1, "Moyen": 2, "Faible": 3}
    if "Risk" in export_df.columns:
        export_df["_sort"] = export_df["Risk"].map(risk_order).fillna(99)
        export_df = export_df.sort_values("_sort").drop(columns="_sort")

    # Traduction des valeurs internes (françaises) vers l'anglais pour
    # l'affichage — appliquée en dernier, après le tri qui dépend encore
    # des valeurs françaises d'origine. Un simple lookup sans effet sur
    # les valeurs qui n'ont pas de traduction connue (noms, dates...).
    for col in export_df.columns:
        export_df[col] = export_df[col].apply(_translate_value)

    return export_df


# ---------------------------------------------------------------------
# EXCEL
# ---------------------------------------------------------------------

def generate_excel_report(df: pd.DataFrame, output_path: str | Path) -> Path:
    output_path = Path(output_path)
    export_df = _prepare_export_df(df)

    wb = Workbook()
    ws_summary = wb.active
    ws_summary.title = "Summary"

    ws_summary["A1"] = "Access Review Report"
    ws_summary["A1"].font = Font(size=14, bold=True)
    ws_summary["A2"] = f"Generated on {datetime.now().strftime('%Y-%m-%d at %H:%M')}"
    ws_summary["A2"].font = Font(italic=True, color="666666")

    ws_summary["A4"] = "Risk Level"
    ws_summary["B4"] = "Number of Accounts"
    ws_summary["A4"].font = ws_summary["B4"].font = Font(bold=True)

    risk_counts = df["risk_level"].value_counts() if "risk_level" in df.columns else {}
    row = 5
    for risk, hex_color in RISK_COLORS_HEX.items():
        count = int(risk_counts.get(risk, 0))
        ws_summary[f"A{row}"] = _translate_value(risk)
        ws_summary[f"B{row}"] = count
        ws_summary[f"A{row}"].fill = PatternFill("solid", fgColor=hex_color)
        ws_summary[f"A{row}"].font = Font(color="FFFFFF", bold=True)
        row += 1

    ws_summary[f"A{row + 1}"] = "Total Accounts Reviewed"
    ws_summary[f"B{row + 1}"] = len(df)
    ws_summary[f"A{row + 1}"].font = Font(bold=True)

    if "is_terminated_but_active" in df.columns:
        ws_summary[f"A{row + 3}"] = "Active Accounts of Departed Employees"
        ws_summary[f"B{row + 3}"] = int(df["is_terminated_but_active"].sum())
    if "is_dormant" in df.columns:
        ws_summary[f"A{row + 4}"] = "Dormant Accounts"
        ws_summary[f"B{row + 4}"] = int(df["is_dormant"].sum())
    if "is_password_stale" in df.columns:
        ws_summary[f"A{row + 5}"] = "Stale Passwords"
        ws_summary[f"B{row + 5}"] = int(df["is_password_stale"].sum())
    if "is_privileged_flag" in df.columns and "has_non_expiring_password" in df.columns:
        ws_summary[f"A{row + 6}"] = "Privileged Accounts with Non-Expiring Password"
        ws_summary[f"B{row + 6}"] = int((df["is_privileged_flag"] & df["has_non_expiring_password"]).sum())
    if "is_duplicate_account" in df.columns:
        ws_summary[f"A{row + 7}"] = "Duplicate Accounts"
        ws_summary[f"B{row + 7}"] = int(df["is_duplicate_account"].sum())

    for col, width in zip("AB", [32, 20]):
        ws_summary.column_dimensions[col].width = width

    ws = wb.create_sheet("Review Plan")
    header_fill = PatternFill("solid", fgColor="1F2937")
    header_font = Font(color="FFFFFF", bold=True)
    thin_border = Border(*[Side(style="thin", color="D9D9D9")] * 4)

    for col_idx, col_name in enumerate(export_df.columns, 1):
        cell = ws.cell(row=1, column=col_idx, value=col_name)
        cell.fill, cell.font = header_fill, header_font
        cell.alignment = Alignment(horizontal="center")
        cell.border = thin_border

    risk_col_idx = (
        list(export_df.columns).index("Risk") + 1 if "Risk" in export_df.columns else None
    )

    for row_idx, record in enumerate(export_df.to_dict("records"), 2):
        for col_idx, (col_name, value) in enumerate(record.items(), 1):
            cell = ws.cell(row=row_idx, column=col_idx, value=value)
            cell.border = thin_border
        if risk_col_idx:
            hex_color = RISK_COLORS_HEX_EN.get(record.get("Risk"))
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
    logger.info(f"Excel report generated: {output_path}")
    return output_path


# ---------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------

def _current_quarter_label() -> str:
    now = datetime.now()
    quarter = (now.month - 1) // 3 + 1
    return f"T{quarter} {now.year}"


def default_report_filename(df: pd.DataFrame, extension: str, extraction_origin: str | None = None) -> str:
    """
    Calcule le nom de fichier recommandé pour un rapport généré :
    'Rapport_revue_acces_<origine>_<date DDMMAAAA>.<extension>' — utilisé
    par le dashboard pour le nom de téléchargement, et disponible pour
    tout appelant qui veut ce même nommage plutôt qu'un nom fixe.

    `extraction_origin` : libellé libre donné par l'utilisateur pour
    l'origine de l'extraction (ex. nom de l'outil source, du périmètre,
    ou tout repère qui lui parle davantage qu'un nom de système ADAP) —
    prioritaire sur le système quand renseigné. Laissé à None ou vide,
    le système est déduit comme avant des valeurs réellement présentes
    dans la colonne 'system' : un seul système -> son nom ; plusieurs
    systèmes distincts -> concaténés par un tiret (borné à 3, au-delà
    "Multi-systemes" pour ne pas produire un nom de fichier interminable) ;
    aucune colonne 'system' ou aucune valeur exploitable -> "Global".
    Les caractères non sûrs pour un nom de fichier (espaces, /, etc.)
    sont remplacés par "_".
    """
    if extraction_origin and extraction_origin.strip():
        system_label = extraction_origin.strip()
    elif "system" in df.columns:
        systems = sorted(df["system"].dropna().astype(str).str.strip().unique())
        systems = [s for s in systems if s]
        if not systems:
            system_label = "Global"
        elif len(systems) <= 3:
            system_label = "-".join(systems)
        else:
            system_label = "Multi-systemes"
    else:
        system_label = "Global"

    system_label = re.sub(r"[^A-Za-z0-9\-]+", "_", system_label).strip("_") or "Global"
    date_label = datetime.now().strftime("%d%m%Y")
    extension = extension.lstrip(".")
    return f"Rapport_revue_acces_{system_label}_{date_label}.{extension}"


# Poids relatifs de largeur par colonne (les colonnes non listées ont un
# poids par défaut de 1.0). "Action recommandée" et "Nom" sont plus larges
# car elles contiennent le texte le plus long — sans ça, ReportLab
# dimensionne les colonnes selon leur seul contenu, sans jamais tenir
# compte de la largeur réelle de la page, d'où un tableau qui déborde.
COLUMN_WIDTH_WEIGHTS = {
    "Account": 1.1,
    "Name": 1.4,
    "Department": 1.0,
    "System": 1.0,
    "Manager": 1.0,
    "Account Status": 0.9,
    "HR Status": 0.9,
    "Days Since Last Login": 0.9,
    "Days Since Password Change": 1.1,
    "Privileged": 0.7,
    "Password Never Expires": 1.0,
    "Recommended Action": 2.2,
    "Risk": 0.8,
    "Last Login (raw)": 1.3,
    "Creation Date": 1.1,
    "Last Password Change (raw)": 1.3,
    "Role": 1.2,
    "Password Status": 1.0,
}
# Colonnes dont le texte doit pouvoir revenir à la ligne plutôt que
# déborder ou être tronqué.
WRAP_COLUMNS = {
    "Account", "Employee ID", "Name", "Department", "System", "Manager",
    "Account Status", "HR Status", "Recommended Action",
    "Last Login (raw)", "Creation Date",
    "Last Password Change (raw)", "Role", "Password Status",
}


def _compute_column_widths(columns: list[str], available_width: float) -> list[float]:
    weights = [COLUMN_WIDTH_WEIGHTS.get(col, 1.0) for col in columns]
    total_weight = sum(weights)
    return [available_width * w / total_weight for w in weights]


def _build_dump_completeness_table(df: pd.DataFrame, available_width: float) -> Table:
    """Tableau du contrôle 1, avec les libellés de colonnes exacts du
    template ('User logon (User ID)', 'User creation DATE', etc.).
    Statut OK/NOK affiché en badge coloré (fond vert/rouge, texte blanc)
    pour un repérage visuel immédiat, cohérent avec le style déjà utilisé
    pour le niveau de risque ailleurs dans le rapport."""
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
        ("ALIGN", (1, 1), (1, -1), "CENTER"),
    ]
    for i, (label, candidates) in enumerate(DUMP_COMPLETENESS_COLUMNS, 1):
        present = any(c in df.columns and df[c].notna().any() for c in candidates)
        bg_color = colors.HexColor("#1E8E5A") if present else colors.HexColor("#C4372B")
        style_commands.append(("BACKGROUND", (1, i), (1, i), bg_color))
        style_commands.append(("TEXTCOLOR", (1, i), (1, i), colors.white))
        style_commands.append(("FONTNAME", (1, i), (1, i), DEFAULT_FONT_BOLD))
    table.setStyle(TableStyle(style_commands))
    return table


def compute_control_coverage(df: pd.DataFrame, comparison_stats: dict) -> list[tuple]:
    """
    Calcul PUR (aucun rendu) de l'état des 18 contrôles — réutilisé à la
    fois par le PDF (_build_control_summary_table) et le dashboard
    (Control Coverage), pour ne jamais dupliquer cette logique.
    Retourne une liste de tuples (numéro, titre, statut, affichage_compte).

    Un compte dont le risque a été accepté pour un constat PRÉCIS
    (ex. 'is_dormant') est exclu du comptage de CE contrôle précis
    uniquement — jamais des autres contrôles, même s'il cumule
    plusieurs problèmes simultanément (voir analysis/risk_acceptance.py
    et is_finding_accepted). Un vrai bug trouvé en testant un compte à
    la fois dormant ET en conflit SoD : une exclusion globale par
    compte aurait aussi fait disparaître le conflit SoD, jamais
    spécifiquement accepté.
    """
    from analysis.risk_acceptance import is_finding_accepted

    def _count_excluding_accepted(key: str) -> int:
        if "accepted_finding_keys" not in df.columns:
            return int(df[key].sum())
        mask = df[key] & ~df.apply(lambda r: is_finding_accepted(r, key), axis=1)
        return int(mask.sum())

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
                count = int(df["account_status"].apply(_is_active_account).astype(bool).sum())
            status = "OK"
            count_display = str(count) if count is not None else "—"
        elif key == "_created":
            # Priorité à la comparaison avec une revue précédente quand
            # elle est fournie : plus fiable qu'une fenêtre de 90 jours
            # fixe (un compte créé il y a 91 jours ressortirait quand
            # même comme "nouveau" par comparaison s'il n'existait pas
            # à la revue précédente). La fenêtre de 90 jours ne sert de
            # repli QUE quand aucune revue précédente n'est fournie.
            value = comparison_stats.get("created")
            if value is not None:
                status, count_display = ("⚠️" if value > 0 else "OK"), str(value)
            elif "is_recently_created" in df.columns and "account_created_date" in df.columns:
                count = int(df["is_recently_created"].sum())
                status, count_display = ("⚠️" if count > 0 else "OK"), str(count)
            else:
                status, count_display = "N/A", "—"
        elif key in ("_reactivated", "_deleted", "_profile_modified"):
            value = comparison_stats.get(key.lstrip("_"))
            if value is None:
                status, count_display = "N/A", "—"
            else:
                status, count_display = ("⚠️" if value > 0 else "OK"), str(value)
        elif key in df.columns:
            count = _count_excluding_accepted(key)
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
    rows = [["No.", "Control", "Result", "Findings"]]
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


def _build_comparison_detail_table(detail: list[dict], field_label: str, available_width: float):
    """
    Tableau de comparaison avant/après pour les contrôles 'Profile
    Modified' et 'Reactivated accounts' : Account / System / ancienne
    valeur + sa date d'extraction / nouvelle valeur + sa date
    d'extraction — uniquement les comptes dont la valeur a réellement
    changé (le detail ne contient déjà que ceux-là). `field_label` est
    'Profile' ou 'Status' selon le contrôle.
    """
    header_style = ParagraphStyle(
        "CompareHeader", fontSize=8, leading=9.5, fontName=DEFAULT_FONT_BOLD, textColor=colors.white,
    )
    cell_style = ParagraphStyle("CompareCell", fontSize=8, leading=9.5, fontName=DEFAULT_FONT)
    headers = ["Account", "System", f"Previous {field_label}", "Extraction Date",
               f"New {field_label}", "Extraction Date"]
    old_key = "old_status" if field_label == "Status" else "old_role"
    new_key = "new_status" if field_label == "Status" else "new_role"
    rows = [[Paragraph(h, header_style) for h in headers]]
    for d in detail:
        rows.append([
            d["username"], d["system"],
            Paragraph(d[old_key] or "—", cell_style), d.get("old_date") or "—",
            Paragraph(d[new_key] or "—", cell_style), d.get("new_date") or "—",
        ])
    weights = [0.14, 0.12, 0.24, 0.14, 0.24, 0.14]  # somme = 1.0
    table = Table(rows, colWidths=[available_width * w for w in weights], repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1F2937")),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#D9D9D9")),
        ("FONTNAME", (0, 0), (-1, -1), DEFAULT_FONT),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
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
    à partir de cette seule section, sans plafond, sur demande explicite :
    la complétude prime sur la longueur du document. Largeurs de
    colonnes proportionnelles et retour à la ligne automatique (même
    infrastructure que le détail principal) — sans quoi un en-tête un
    peu long chevauche son voisin.
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
            value = _translate_value(value)
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


def _build_owner_tracking_table(available_width: float) -> Table:
    """
    Petit tableau de suivi (Owner / Comment / Due Date / Status), vide,
    ajouté après CHAQUE section de contrôle — pour que le propriétaire
    du système puisse commenter et dater la remédiation directement dans
    le document, sans avoir à en tenir un séparé. Présent pour les 18
    sections sans exception, y compris quand aucun compte n'est
    concerné : le propriétaire doit pouvoir attester explicitement
    "revu, rien à signaler" avec sa propre date, pas seulement les
    sections qui ont des comptes à traiter. Ligne de saisie volontairement
    haute : ce tableau peut être imprimé et rempli à la main.
    """
    header_style = ParagraphStyle(
        "OwnerHeader", fontSize=8, leading=10, fontName=DEFAULT_FONT_BOLD, textColor=colors.white,
    )
    rows = [[Paragraph(h, header_style) for h in ("Owner", "Comment", "Due Date", "Status")]]
    rows.append(["", "", "", ""])
    col_widths = [available_width * w for w in (0.18, 0.44, 0.16, 0.22)]
    table = Table(rows, colWidths=col_widths, rowHeights=[16, 42])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#4B5563")),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#D9D9D9")),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("VALIGN", (0, 0), (0, 0), "MIDDLE"),
        ("VALIGN", (0, 1), (-1, 1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, 0), 4),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 4),
        ("TOPPADDING", (0, 1), (-1, 1), 6),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
    ]))
    return table


def _build_signoff_block(roles: list[str], filled_values: list[str | None], available_width: float) -> Table:
    """
    Bloc de signature à 3 colonnes (un rôle par colonne), pensé pour être
    imprimé et signé à la main : nom déjà connu affiché si fourni, sinon
    case vide (jamais de texte "[TO BE COMPLETED]" qui gênerait l'écriture
    manuscrite) ; ligne signature bien plus haute qu'un simple libellé,
    avec juste une légère mention "Signature & Date" en petit gris pour
    indiquer l'usage sans encombrer l'espace d'écriture.
    """
    caption_style = ParagraphStyle(
        "SignoffCaption", fontSize=7, leading=8, fontName=DEFAULT_FONT,
        textColor=colors.HexColor("#9CA3AF"), alignment=1,
    )
    header_row = [f"{r}:" for r in roles]
    name_row = [v if v else "" for v in filled_values]
    signature_row = [Paragraph("Signature &amp; Date", caption_style) for _ in roles]
    table = Table(
        [header_row, name_row, signature_row],
        colWidths=[available_width / 3] * 3,
        rowHeights=[22, 34, 60],
    )
    table.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#D9D9D9")),
        ("FONTNAME", (0, 0), (-1, -1), DEFAULT_FONT),
        ("FONTNAME", (0, 0), (-1, 0), DEFAULT_FONT_BOLD),
        ("FONTSIZE", (0, 0), (-1, 1), 9),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, 1), "MIDDLE"),
        ("VALIGN", (0, 2), (-1, 2), "BOTTOM"),
        ("BOTTOMPADDING", (0, 2), (-1, 2), 4),
        ("TOPPADDING", (0, 0), (-1, 1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, 1), 6),
    ]))
    return table



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

    Chaque sous-section se termine par un petit tableau de suivi
    (Owner / Comment / Due Date / Status) — APRÈS le tableau nominatif
    des comptes plutôt qu'avant : le propriétaire doit d'abord voir CE
    qui est signalé avant de pouvoir commenter/dater l'action, l'ordre
    inverse l'obligerait à faire l'aller-retour.
    """
    # Un compte dont le risque a été accepté pour un constat PRÉCIS n'est
    # exclu que du tableau du contrôle correspondant — jamais des autres,
    # même s'il cumule plusieurs problèmes simultanément (voir
    # is_finding_accepted plus bas et analysis/risk_acceptance.py).
    from analysis.risk_acceptance import is_finding_accepted

    elements = []
    for number, title, guidance, key in CONTROL_SUBSECTIONS:
        elements.append(Paragraph(f"{number}.{title}", system_style))
        if guidance:
            elements.append(Paragraph(guidance, note_style))

        count = None
        note = None
        subset = None
        comparison_detail = None
        if key is None:
            note = "N/A — requires company-specific configuration, not derivable from the ingested data alone."
        elif key == "_active_count":
            if "account_status" in df.columns:
                subset = df[df["account_status"].apply(_is_active_account)]
                count = len(subset)
            else:
                note = "N/A — 'account_status' column missing."
        elif key == "_deleted":
            # Un compte supprimé, par définition, n'existe plus dans le
            # fichier ACTUEL — le chercher dans `df` renverrait toujours
            # zéro résultat. Il ne peut être retrouvé (pour afficher ses
            # attributs) que dans la revue PRÉCÉDENTE.
            value = comparison_stats.get("deleted")
            if value is None:
                note = "N/A — no previous review provided to establish the comparison."
            else:
                count = value
                names = comparison_stats.get("deleted_accounts") or []
                if names and previous_df is not None and "username" in previous_df.columns:
                    subset = previous_df[previous_df["username"].astype(str).isin(names)]
        elif key == "_created":
            value = comparison_stats.get("created")
            if value is not None:
                count = value
                names = comparison_stats.get("created_accounts") or []
                if names and "username" in df.columns:
                    subset = df[df["username"].astype(str).isin(names)]
            elif "is_recently_created" in df.columns and "account_created_date" in df.columns:
                subset = df[df["is_recently_created"] == True]  # noqa: E712
                count = len(subset)
            else:
                note = (
                    "N/A — no previous review provided to establish the comparison, "
                    "and no 'account_created_date' column either."
                )
        elif key == "_reactivated":
            value = comparison_stats.get("reactivated")
            if value is None:
                note = "N/A — no previous review provided to establish the comparison."
            else:
                count = value
                comparison_detail = comparison_stats.get("reactivated_detail") or []
        elif key == "_profile_modified":
            value = comparison_stats.get("profile_modified")
            if value is None:
                note = "N/A — no previous review provided to establish the comparison."
            else:
                count = value
                comparison_detail = comparison_stats.get("profile_modified_detail") or []
        elif key in df.columns:
            subset = df[df[key] == True]  # noqa: E712 (comparaison explicite voulue sur une colonne booléenne)
            if len(subset):
                subset = subset[~subset.apply(lambda r: is_finding_accepted(r, key), axis=1)]
            count = len(subset)
        else:
            note = f"N/A — column '{key}' missing from the ingested data."

        if count is not None:
            elements.append(Paragraph(f"<b>{count}</b> account(s) concerned.", action_style))
            elements.append(Spacer(1, 0.1 * cm))
            elements.append(_build_owner_tracking_table(available_width))
            if comparison_detail:
                elements.append(Spacer(1, 0.15 * cm))
                field_label = "Status" if key == "_reactivated" else "Profile"
                elements.append(_build_comparison_detail_table(comparison_detail, field_label, available_width))
            elif subset is not None and len(subset):
                elements.append(Spacer(1, 0.15 * cm))
                table_cols = CONTROL_TABLE_COLUMNS.get(key)
                elements.extend(_build_capped_account_table(subset, available_width, columns=table_cols))
        else:
            elements.append(Paragraph(note, note_style))
            elements.append(Spacer(1, 0.1 * cm))
            elements.append(_build_owner_tracking_table(available_width))
        elements.append(Spacer(1, 0.25 * cm))
    return elements


def _build_review_comparison_section(
    df: pd.DataFrame, previous_df, section_style, note_style, available_width,
    current_extraction_date: str = "", previous_extraction_date: str = "",
):
    """
    Section 'a. Summary of the review' : répartition des comptes par
    statut, comparée au cycle précédent si `previous_df` est fourni —
    calculée à partir des données réelles, pas déclarative.

    `current_extraction_date` / `previous_extraction_date` : dates
    d'extraction des deux fichiers comparés (texte libre, ex.
    '2026-09-12'), reportées dans le détail des comptes créés/supprimés/
    réactivés/profils modifiés pour que le tableau de comparaison précise
    QUAND chaque valeur a été observée, pas seulement CE QUI a changé.

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
        "profile_modified_detail": None, "reactivated_detail": None,
    }
    elements = [Paragraph("a. Summary of the review", section_style)]
    if "account_status" not in df.columns:
        elements.append(Paragraph(
            "'account_status' column missing: breakdown by status unavailable.", note_style,
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
            # Comparaison sur une clé NORMALISÉE (espaces/casse), pas sur le nom
            # brut : deux cycles de revue peuvent provenir d'exports légèrement
            # différents (ex. 'jdupont' vs 'JDupont' si l'outil d'export a
            # changé entre deux mois) — sans cette normalisation, le MÊME
            # compte serait signalé à tort comme supprimé puis recréé, un faux
            # signal trompeur pour un rapport d'audit. Le nom d'affichage
            # original (première valeur rencontrée) reste utilisé partout
            # ailleurs.
            def _norm_key(v):
                return str(v).strip().lower()

            current_series = df[key_col].dropna()
            previous_series = previous_df[key_col].dropna()
            current_display = {_norm_key(v): v for v in reversed(current_series.tolist())}
            previous_display = {_norm_key(v): v for v in reversed(previous_series.tolist())}
            current_keys = set(current_display)
            previous_keys = set(previous_display)
            created = current_keys - previous_keys
            deleted = previous_keys - current_keys
            common = current_keys & previous_keys

            reactivated_accounts = []
            profile_modified_accounts = []
            escalated_accounts = []
            # Détail nominatif (pas seulement le nom) : ancien profil/statut
            # ET nouveau, chacun avec sa propre date d'extraction — pour
            # produire le tableau de comparaison demandé (User / System /
            # ancien profil + date / nouveau profil + date), pas juste un
            # compte sans contexte de CE qui a changé.
            profile_modified_detail = []
            reactivated_detail = []
            if common:
                curr_idx = df.set_index(df[key_col].map(_norm_key))
                prev_idx = previous_df.set_index(previous_df[key_col].map(_norm_key))
                for norm_uname in common:
                    curr_row = curr_idx.loc[norm_uname]
                    prev_row = prev_idx.loc[norm_uname]
                    uname = current_display[norm_uname]
                    if isinstance(curr_row, pd.DataFrame):
                        curr_row = curr_row.iloc[0]
                    if isinstance(prev_row, pd.DataFrame):
                        prev_row = prev_row.iloc[0]
                    if "account_status" in df.columns:
                        was_inactive = not _is_active_account(prev_row.get("account_status"))
                        is_active_now = _is_active_account(curr_row.get("account_status"))
                        if was_inactive and is_active_now:
                            reactivated_accounts.append(str(uname))
                            reactivated_detail.append({
                                "username": str(uname), "system": str(curr_row.get("system", "")),
                                "old_status": str(prev_row.get("account_status", "")),
                                "old_date": previous_extraction_date or "",
                                "new_status": str(curr_row.get("account_status", "")),
                                "new_date": current_extraction_date or "",
                            })
                    if "role" in df.columns:
                        old_role, new_role = str(prev_row.get("role")), str(curr_row.get("role"))
                        if old_role != new_role:
                            profile_modified_accounts.append(str(uname))
                            profile_modified_detail.append({
                                "username": str(uname), "system": str(curr_row.get("system", "")),
                                "old_role": old_role, "old_date": previous_extraction_date or "",
                                "new_role": new_role, "new_date": current_extraction_date or "",
                            })
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
            created_display = sorted(current_display[k] for k in created)
            deleted_display = sorted(previous_display[k] for k in deleted)
            stats.update({
                "created": len(created), "deleted": len(deleted),
                "reactivated": reactivated, "profile_modified": profile_modified,
                "privilege_escalation": len(escalated_accounts),
                "created_accounts": created_display, "deleted_accounts": deleted_display,
                "reactivated_accounts": reactivated_accounts,
                "profile_modified_accounts": profile_modified_accounts,
                "privilege_escalation_accounts": escalated_accounts,
                "profile_modified_detail": profile_modified_detail,
                "reactivated_detail": reactivated_detail,
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
                    "<b>Privilege Escalation — accounts concerned:</b> "
                    + ", ".join(escalated_accounts[:20])
                    + (f" (+{len(escalated_accounts) - 20} more)" if len(escalated_accounts) > 20 else ""),
                    note_style,
                ))
    else:
        elements.append(Paragraph(
            "The review of the application accounts covers a total of accounts distributed as "
            "follows (no previous review provided for comparison):",
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


def _docx_set_cell(
    cell, text: str, bold: bool = False, color: RGBColor | None = None, size: int = 9,
    style_name: str | None = None,
) -> None:
    cell.text = ""
    run = cell.paragraphs[0].add_run(str(text))
    if style_name:
        # Style nommé (défini une fois dans _add_custom_docx_styles) :
        # réduit fortement le volume de XML comparé à une mise en forme
        # directe répétée sur chaque cellule — la définition vit une
        # seule fois dans styles.xml, chaque passage ne fait qu'y
        # référer par nom.
        run.style = style_name
    else:
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
    if col_widths_cm:
        # Sans ceci, Word (Desktop ET surtout Word Online, qui s'est
        # montré plus strict) reste libre de RECALCULER les largeurs de
        # colonne selon le contenu, ignorant les largeurs de cellule
        # qu'on impose explicitement plus bas — une colonne étroite
        # voulue (ex. 'SN') peut alors se retrouver bien plus large que
        # prévu, allongeant tout le tableau. Désactiver l'ajustement
        # automatique ET renseigner la grille de colonnes au niveau de
        # la TABLE (pas seulement cellule par cellule) force Word à
        # respecter les largeurs demandées.
        table.autofit = False
        table.allow_autofit = False
        for j, width_cm in enumerate(col_widths_cm):
            table.columns[j].width = Cm(width_cm)
    # table.cell(i, j) reconstruit TOUTE la structure de fusion de cellules
    # de la table depuis le XML à CHAQUE appel (limitation connue de
    # python-docx) — utilisé dans une double boucle, ça rend le
    # remplissage quadratique en nombre de lignes (constaté : ~13s pour
    # seulement 100 lignes, aurait dépassé 5 minutes pour 500 comptes).
    # table.rows[i].cells[j] ne scanne que la ligne concernée, pas toute
    # la table — même résultat, sans le recalcul répété.
    for i, (table_row, row) in enumerate(zip(table.rows, rows)):
        cells = table_row.cells
        for j, value in enumerate(row):
            cell = cells[j]
            if header and i == 0:
                _docx_set_cell(cell, value, style_name="MTN Table Header")
                _docx_shade_cell(cell, "1F2937")
            else:
                _docx_set_cell(cell, value, style_name="MTN Table Body")
            if col_widths_cm:
                cell.width = Cm(col_widths_cm[j])


def _docx_add_comparison_detail_table(doc, detail: list[dict], field_label: str) -> None:
    """Équivalent Word de _build_comparison_detail_table (PDF) : tableau
    avant/après avec les deux dates d'extraction, pour les contrôles
    'Profile Modified' et 'Reactivated accounts'."""
    headers = ["Account", "System", f"Previous {field_label}", "Extraction Date",
               f"New {field_label}", "Extraction Date"]
    old_key = "old_status" if field_label == "Status" else "old_role"
    new_key = "new_status" if field_label == "Status" else "new_role"
    rows = [headers]
    for d in detail:
        rows.append([
            d["username"], d["system"], d[old_key] or "—", d.get("old_date") or "—",
            d[new_key] or "—", d.get("new_date") or "—",
        ])
    _docx_add_table(doc, rows, col_widths_cm=[2.3, 2, 4, 3, 4, 3])


def _docx_add_dump_completeness_table(doc, dump_rows: list) -> None:
    """Équivalent Word de _build_dump_completeness_table (PDF) : même
    tableau Field/Status, avec le statut affiché en badge coloré
    (fond vert pour OK, rouge pour NOK, texte blanc) plutôt qu'en texte
    brut — cohérent avec le rendu PDF."""
    table = doc.add_table(rows=len(dump_rows), cols=2)
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.style = "Table Grid"
    for i, (table_row, row) in enumerate(zip(table.rows, dump_rows)):
        cells = table_row.cells
        for j, value in enumerate(row):
            cell = cells[j]
            if i == 0:
                _docx_set_cell(cell, value, style_name="MTN Table Header")
                _docx_shade_cell(cell, "1F2937")
            elif j == 1:
                _docx_set_cell(cell, value, style_name="MTN Table Header")
                _docx_shade_cell(cell, "1E8E5A" if value == "OK" else "C4372B")
                cell.paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER
            else:
                _docx_set_cell(cell, value, style_name="MTN Table Body")


def _docx_add_owner_tracking_table(doc) -> None:
    """Équivalent Word de _build_owner_tracking_table (PDF) : petit
    tableau vide Owner / Comment / Due Date / Status, ajouté après
    CHAQUE section de contrôle pour que le propriétaire puisse
    commenter et dater la remédiation directement dans le document.
    Ligne de saisie volontairement haute : peut être imprimé et rempli
    à la main."""
    from docx.enum.table import WD_ROW_HEIGHT_RULE
    table = doc.add_table(rows=2, cols=4)
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.style = "Table Grid"
    headers = ["Owner", "Comment", "Due Date", "Status"]
    blank = ["", "", "", ""]
    for i, (table_row, values) in enumerate(zip(table.rows, [headers, blank])):
        cells = table_row.cells
        for j, value in enumerate(values):
            if i == 0:
                _docx_set_cell(cells[j], value, style_name="MTN Table Header")
                _docx_shade_cell(cells[j], "4B5563")
            else:
                _docx_set_cell(cells[j], value, style_name="MTN Table Body")
        if i == 1:
            table_row.height = Cm(1.1)
            table_row.height_rule = WD_ROW_HEIGHT_RULE.AT_LEAST


def _docx_add_signoff_block(doc, roles: list[str], filled_values: list[str | None]) -> None:
    """
    Équivalent Word de _build_signoff_block (PDF) : bloc de signature à
    3 colonnes, pensé pour être imprimé et signé à la main — nom déjà
    connu affiché si fourni, sinon case vide (jamais de texte
    "[TO BE COMPLETED]" qui gênerait l'écriture manuscrite), et une
    ligne signature nettement plus haute qu'un simple libellé.
    """
    from docx.enum.table import WD_ROW_HEIGHT_RULE
    table = doc.add_table(rows=3, cols=3)
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.style = "Table Grid"
    header_row, name_row, signature_row = table.rows
    for j, role in enumerate(roles):
        _docx_set_cell(header_row.cells[j], f"{role}:", bold=True, size=9)
        _docx_set_cell(name_row.cells[j], filled_values[j] or "", size=9)
        _docx_set_cell(signature_row.cells[j], "Signature & Date", size=7)
        for run in signature_row.cells[j].paragraphs[0].runs:
            run.font.color.rgb = RGBColor(0x9C, 0xA3, 0xAF)
    name_row.height = Cm(1.0)
    name_row.height_rule = WD_ROW_HEIGHT_RULE.AT_LEAST
    signature_row.height = Cm(1.8)
    signature_row.height_rule = WD_ROW_HEIGHT_RULE.AT_LEAST
    for cell in signature_row.cells:
        cell.paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER


def _add_custom_docx_styles(doc) -> None:
    """
    Définit une fois pour toutes deux styles de caractère réutilisables
    (en-tête de tableau blanc/gras, corps de tableau standard), plutôt
    que de répéter la même mise en forme directe (w:rPr complet : gras,
    couleur, taille) sur CHACUNE des centaines de cellules du document.

    Trouvé en résolvant un vrai problème de compatibilité Word Online/
    SharePoint : le rapport complet (43 tableaux) s'ouvrait normalement
    dans Word Desktop mais pas dans Word Online, alors que le XML est
    parfaitement valide — la cause la plus probable, une fois la
    validité du schéma exclue, est la complexité/volume du document.
    Utiliser de vrais styles Word (définis une seule fois dans
    styles.xml, référencés par nom dans chaque passage plutôt que
    dupliqués) est la pratique standard recommandée pour réduire cette
    complexité sans retirer le moindre tableau ni changer le rendu
    visuel.
    """
    from docx.enum.style import WD_STYLE_TYPE

    styles = doc.styles
    if "MTN Table Header" not in [s.name for s in styles]:
        header_style = styles.add_style("MTN Table Header", WD_STYLE_TYPE.CHARACTER)
        header_style.font.name = DEFAULT_FONT_BOLD
        header_style.font.size = Pt(9)
        header_style.font.bold = True
        header_style.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
    if "MTN Table Body" not in [s.name for s in styles]:
        body_style = styles.add_style("MTN Table Body", WD_STYLE_TYPE.CHARACTER)
        body_style.font.name = DEFAULT_FONT
        body_style.font.size = Pt(8.5)


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
    current_extraction_date: str | None = None,
    previous_extraction_date: str | None = None,
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

    # Métadonnées du document : python-docx laisse par défaut une date
    # figée (2013, celle du modèle interne) et un auteur vide — un
    # document avec des métadonnées manifestement incohérentes (créé et
    # modifié "en 2013" alors qu'il vient d'être généré) est un signal de
    # non-fiabilité pour tout système qui les affiche (SharePoint,
    # historique de versions...), même sans qu'on puisse garantir que
    # c'est la cause exacte d'un blocage d'édition observé.
    now = datetime.now()
    doc.core_properties.author = editor or "Access Review Toolkit"
    doc.core_properties.last_modified_by = editor or "Access Review Toolkit"
    doc.core_properties.created = now
    doc.core_properties.modified = now
    doc.core_properties.title = "Application Accounts Review"
    doc.core_properties.subject = application_scope or ""

    _add_custom_docx_styles(doc)

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
    _docx_add_signoff_block(
        doc, ["Control Performer", "Manager HUB", "HUB senior Manager LISO"],
        [prepared_by, reviewed_by, approved_by],
    )
    doc.add_paragraph()
    _docx_add_signoff_block(
        doc, ["SYSTEM OWNER", "OPCOS LISO", "SM Information Security OPCOS"],
        [None, None, None],
    )
    doc.add_paragraph()

    # ---- I. OBJECTIVE ----
    doc.add_heading("I. OBJECTIVE", level=1)
    doc.add_paragraph(OBJECTIVE_INTRO)
    for b in OBJECTIVE_BULLETS:
        doc.add_paragraph(b, style="List Bullet")
    doc.add_paragraph(OBJECTIVE_CONTROL_INTRO)
    controls_rows = [["SN", "Control", "Control Description/Expectations"]]
    for sn, name, desc in TEMPLATE_CONTROLS:
        controls_rows.append([str(sn), name, desc.replace("\n", " ")])
    _docx_add_table(doc, controls_rows, col_widths_cm=[1.2, 3.8, 12])
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
        f"Methodology: an account is considered \u201cdormant\u201d if it has not logged in for more than "
        f"{dormant_threshold_days} days — including an account that has never recorded a single "
        f"login since its creation. This tool never recommends deleting an account, only "
        f"disabling it — reversible, and applicable without prior history."
    )

    # Réutilise le calcul de comparaison déjà construit pour le PDF — même
    # source de données, pas de logique dupliquée. Les éléments ReportLab
    # retournés sont ignorés ici, seul le dict `stats` (pur) est utilisé.
    _dummy_style = ParagraphStyle("Dummy")
    _, comparison_stats = _build_review_comparison_section(
        df, previous_df, _dummy_style, _dummy_style, 100,
        current_extraction_date=current_extraction_date or "",
        previous_extraction_date=previous_extraction_date or "",
    )

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
                    names_text += f" (+{len(escalated) - 20} more)"
                p = doc.add_paragraph()
                p.add_run("Privilege Escalation — accounts concerned: ").bold = True
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
        "Note: the \u201cRecommended Action\u201d column always reflects the account's overall priority "
        "action (across all controls), not necessarily the precise reason for its presence "
        "in the current subsection — an account may appear in several sections at once."
    )
    clarif_p.runs[0].italic = True
    clarif_p.runs[0].font.size = Pt(8.5)

    doc.add_heading("Control Summary", level=2)
    # Un compte dont le risque a été accepté pour un constat PRÉCIS n'est
    # exclu que du comptage/tableau du contrôle correspondant — jamais
    # des autres, même s'il cumule plusieurs problèmes simultanément
    # (voir is_finding_accepted et analysis/risk_acceptance.py).
    # df_all_accounts préserve l'ensemble complet pour la section
    # Exceptions plus bas.
    df_all_accounts = df
    from analysis.risk_acceptance import is_finding_accepted
    summary_rows = [["No.", "Control", "Result", "Findings"]]
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
            count_display = str(int(df["account_status"].apply(_is_active_account).astype(bool).sum())) if "account_status" in df.columns else "—"
        elif key == "_created":
            value = comparison_stats.get("created")
            if value is not None:
                status, count_display = ("⚠" if value > 0 else "OK"), str(value)
            elif "is_recently_created" in df.columns and "account_created_date" in df.columns:
                count = int(df["is_recently_created"].sum())
                status, count_display = ("⚠" if count > 0 else "OK"), str(count)
            else:
                status, count_display = "N/A", "—"
        elif key in ("_deleted", "_reactivated", "_profile_modified"):
            value = comparison_stats.get(key.lstrip("_"))
            status, count_display = ("N/A", "—") if value is None else (("⚠" if value > 0 else "OK"), str(value))
        elif key in df.columns:
            if "accepted_finding_keys" in df.columns:
                mask = df[key] & ~df.apply(lambda r: is_finding_accepted(r, key), axis=1)
                count = int(mask.sum())
            else:
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
    _docx_add_dump_completeness_table(doc, dump_rows)
    doc.add_paragraph()
    _docx_add_owner_tracking_table(doc)
    doc.add_paragraph()

    for number, ctrl_title, guidance, key in CONTROL_SUBSECTIONS:
        doc.add_heading(f"{number}.{ctrl_title}", level=2)
        if guidance:
            doc.add_paragraph(guidance)
        count, note, subset, comparison_detail = None, None, None, None
        if key is None:
            note = "N/A — requires company-specific configuration, not derivable from the ingested data alone."
        elif key == "_active_count":
            if "account_status" in df.columns:
                subset = df[df["account_status"].apply(_is_active_account)]
                count = len(subset)
            else:
                note = "N/A — 'account_status' column missing."
        elif key == "_deleted":
            # Un compte supprimé n'existe plus dans le fichier ACTUEL —
            # uniquement retrouvable dans la revue précédente.
            value = comparison_stats.get("deleted")
            if value is None:
                note = "N/A — no previous review provided to establish the comparison."
            else:
                count = value
                names = comparison_stats.get("deleted_accounts") or []
                if names and previous_df is not None and "username" in previous_df.columns:
                    subset = previous_df[previous_df["username"].astype(str).isin(names)]
        elif key == "_created":
            value = comparison_stats.get("created")
            if value is not None:
                count = value
                names = comparison_stats.get("created_accounts") or []
                if names and "username" in df.columns:
                    subset = df[df["username"].astype(str).isin(names)]
            elif "is_recently_created" in df.columns and "account_created_date" in df.columns:
                subset = df[df["is_recently_created"] == True]  # noqa: E712
                count = len(subset)
            else:
                note = (
                    "N/A — no previous review provided to establish the comparison, "
                    "and no 'account_created_date' column either."
                )
        elif key == "_reactivated":
            value = comparison_stats.get("reactivated")
            if value is None:
                note = "N/A — no previous review provided to establish the comparison."
            else:
                count = value
                comparison_detail = comparison_stats.get("reactivated_detail") or []
        elif key == "_profile_modified":
            value = comparison_stats.get("profile_modified")
            if value is None:
                note = "N/A — no previous review provided to establish the comparison."
            else:
                count = value
                comparison_detail = comparison_stats.get("profile_modified_detail") or []
        elif key in df.columns:
            subset = df[df[key] == True]  # noqa: E712
            if len(subset):
                subset = subset[~subset.apply(lambda r: is_finding_accepted(r, key), axis=1)]
            count = len(subset)
        else:
            note = f"N/A — column '{key}' missing from the ingested data."

        if count is not None:
            p = doc.add_paragraph()
            p.add_run(f"{count} account(s) concerned.").bold = True
            doc.add_paragraph()
            _docx_add_owner_tracking_table(doc)
            if comparison_detail:
                doc.add_paragraph()
                field_label = "Status" if key == "_reactivated" else "Profile"
                _docx_add_comparison_detail_table(doc, comparison_detail, field_label)
            elif subset is not None and len(subset):
                default_cols = ["username", "full_name", "system", "review_action"]
                cols = [c for c in (CONTROL_TABLE_COLUMNS.get(key) or default_cols) if c in subset.columns]
                if cols:
                    doc.add_paragraph()
                    display = subset[cols].fillna("").astype(str).map(_translate_value)
                    detail_rows = [[ALL_COLUMN_LABELS.get(c, c) for c in cols]] + display.values.tolist()
                    _docx_add_table(doc, detail_rows)
        else:
            doc.add_paragraph(note)
            doc.add_paragraph()
            _docx_add_owner_tracking_table(doc)
        doc.add_paragraph()

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

    # ---- Operational Annex ----
    doc.add_heading("Operational Annex — Actionable Cycle Detail", level=1)

    quality_report = compute_data_quality_report(df)
    doc.add_heading(f"Data Quality — estimated reliability {quality_report['reliability_pct']}%", level=2)
    doc.add_paragraph(
        "Preliminary check of the source file's reliability, ahead of the IAM controls "
        "themselves — purely informational, does not alter any data or analysis result."
    )
    issue_labels = {
        "username_missing": "Missing account identifiers",
        "duplicate_usernames": "Duplicate accounts (same identifier + system)",
        "invalid_dates": "Unparseable last login dates",
        "future_dates": "Last login dates in the future",
        "unknown_status": "Unrecognized account statuses",
        "system_missing": "System not specified",
        "manager_missing": "Manager not specified",
    }
    quality_rows = [["Indicator", "Value"], ["Rows analyzed", str(quality_report["total_rows"])]]
    for key, label in issue_labels.items():
        count = quality_report["issues"].get(key)
        if count:
            quality_rows.append([label, str(count)])
    if len(quality_rows) > 2:
        _docx_add_table(doc, quality_rows)
    else:
        doc.add_paragraph("No data quality issues detected in this file.")
    doc.add_paragraph()

    doc.add_heading("Executive Summary", level=2)
    # 'Total Accounts Reviewed' reste le vrai total de la population
    # revue (acceptés compris) — risk_level est déjà recalculé par
    # apply_risk_acceptances en excluant les constats acceptés. Les
    # comptages BRUTS par indicateur ci-dessous excluent spécifiquement
    # les comptes acceptés POUR CE constat précis (is_finding_accepted),
    # jamais une exclusion globale du compte.
    total_accounts_reviewed = len(df_all_accounts)

    def _count_excluding_accepted_word(flag_col: str) -> int:
        if "accepted_finding_keys" not in df.columns:
            return int(df[flag_col].sum())
        mask = df[flag_col] & ~df.apply(lambda r: is_finding_accepted(r, flag_col), axis=1)
        return int(mask.sum())

    risk_counts = df["risk_level"].value_counts() if "risk_level" in df.columns else {}
    exec_rows = [["Indicator", "Value"], ["Total Accounts Reviewed", str(total_accounts_reviewed)]]
    for risk in RISK_COLORS_HEX:
        exec_rows.append([_translate_value(risk), str(int(risk_counts.get(risk, 0)))])
    if "is_terminated_but_active" in df.columns:
        exec_rows.append(["Active Accounts of Departed Employees", str(_count_excluding_accepted_word("is_terminated_but_active"))])
    if "is_dormant" in df.columns:
        exec_rows.append(["Dormant Accounts", str(_count_excluding_accepted_word("is_dormant"))])
    if "is_never_used" in df.columns:
        exec_rows.append(["Never Used Accounts", str(_count_excluding_accepted_word("is_never_used"))])
    if "is_password_stale" in df.columns:
        exec_rows.append(["Stale Passwords", str(_count_excluding_accepted_word("is_password_stale"))])
    if "is_duplicate_account" in df.columns:
        exec_rows.append(["Duplicate Accounts", str(_count_excluding_accepted_word("is_duplicate_account"))])
    if "is_locked" in df.columns:
        exec_rows.append(["Locked Accounts (outside dormancy)", str(_count_excluding_accepted_word("is_locked"))])
    _docx_add_table(doc, exec_rows)
    doc.add_paragraph()

    # ---- Exceptions : comptes dont le risque a été formellement accepté ----
    df = df_all_accounts  # restaure l'ensemble complet (voir plus haut)
    doc.add_heading("Exceptions — Risques acceptés", level=2)
    accepted_detail = get_accepted_findings_detail(df)
    if accepted_detail:
        exc_labels = ["Account", "System", "Accepted Finding", "Justification", "Accepted By", "Expiration"]
        exc_rows_data = [
            [d["username"], d["system"], d["accepted_finding"], d["comment"], d["accepted_by"], d["expiration_date"] or "—"]
            for d in accepted_detail
        ]
        _docx_add_table(doc, [exc_labels] + exc_rows_data)
    else:
        doc.add_paragraph("No risk acceptance recorded for this cycle.")
    has_expired = "expired_finding_keys" in df.columns and df["expired_finding_keys"].apply(len).gt(0).any()
    if has_expired:
        expired_names = df.loc[df["expired_finding_keys"].apply(len).gt(0), "username"].astype(str).tolist()
        p = doc.add_paragraph()
        p.add_run(
            f"{len(expired_names)} account(s) had a risk acceptance that has EXPIRED and "
            f"require re-validation (now listed as active findings again): {', '.join(expired_names)}."
        ).italic = True
    doc.add_paragraph()

    doc.add_heading("Annexes", level=2)

    # Sections A à F laissées vides à la demande explicite de l'utilisateur
    # (équipe MTN) : seuls les titres sont générés, le contenu est ajouté
    # manuellement après coup — pas de tableau ni de donnée pré-remplie.
    for letter, title in [
        ("A", "User access form of created accounts"),
        ("B", "Justification of Reactivated accounts"),
        ("C", "Rationale for Profile Change"),
        ("D", "List Of Users Used for the review"),
        ("F", "First List user access review Report"),
    ]:
        doc.add_heading(f"{letter}. {title}", level=3)
        doc.add_paragraph()
        doc.add_paragraph()

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
    current_extraction_date: str | None = None,
    previous_extraction_date: str | None = None,
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
    section_style = ParagraphStyle("SectionH", parent=styles["Heading2"], spaceBefore=14, spaceAfter=6, fontName=DEFAULT_FONT_BOLD)
    system_style = ParagraphStyle(
        "SystemH", parent=styles["Heading3"], textColor=colors.HexColor("#1F2937"),
        spaceBefore=12, spaceAfter=4, fontName=DEFAULT_FONT_BOLD,
    )
    note_style = ParagraphStyle("Note", parent=styles["Normal"], fontSize=8.5, textColor=colors.grey, spaceAfter=10, fontName=DEFAULT_FONT)
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
    elements.append(_build_signoff_block(
        ["Control Performer", "Manager HUB", "HUB senior Manager LISO"],
        [prepared_by, reviewed_by, approved_by],
        available_width,
    ))
    elements.append(Spacer(1, 0.3 * cm))
    elements.append(_build_signoff_block(
        ["SYSTEM OWNER", "OPCOS LISO", "SM Information Security OPCOS"],
        [None, None, None],
        available_width,
    ))
    elements.append(Spacer(1, 0.6 * cm))
    elements.append(Spacer(1, 0.6 * cm))

    # ---- I. OBJECTIVE ----
    elements.append(Paragraph("I. OBJECTIVE", section_style))
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
        colWidths=[available_width * w for w in (0.03, 0.20, 0.77)],
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
            f"⚠ WARNING: {ocr_count} account(s) in this report were extracted via optical "
            f"character recognition (OCR) from an image, not a structured file. OCR can "
            f"introduce reading errors (e.g. '1' read as 'l', '0' read as 'O'). These "
            f"accounts must be manually verified before any decision — do not give them "
            f"the same confidence as the others.",
            ocr_warning_style,
        ))

    elements.append(Paragraph(
        f"Methodology: an account is considered \u201cdormant\u201d if it has not logged in for more than "
        f"{dormant_threshold_days} days — including an account that has never recorded a single "
        f"login since its creation. Each account receives a risk level and a recommended "
        f"action based on its status (active account of a departed employee, dormant "
        f"privileged account, no identified manager). This tool never recommends deleting "
        f"an account, only disabling it — reversible, and applicable without prior "
        f"history.",
        note_style,
    ))

    # ---- III.a Summary of the review (comparaison avec la revue précédente) ----
    comparison_elements, comparison_stats = _build_review_comparison_section(
        df, previous_df, section_style, note_style, available_width,
        current_extraction_date=current_extraction_date or "",
        previous_extraction_date=previous_extraction_date or "",
    )
    elements.extend(comparison_elements)
    elements.append(Spacer(1, 0.4 * cm))

    # ---- IV. ACCOUNT DETAILS BY CONTROL (18 sous-sections fidèles au template) ----
    elements.append(Paragraph("IV. ACCOUNT DETAILS BY CONTROL", section_style))
    elements.append(Paragraph(SECTION_IV_INTRO, note_style))
    elements.append(Paragraph(
        "Note: the \u201cRecommended Action\u201d column always reflects the account's overall priority "
        "action (across all controls), not necessarily the precise reason for its presence "
        "in the current subsection — an account may appear in several sections at once.",
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
    elements.append(Spacer(1, 0.12 * cm))
    elements.append(_build_owner_tracking_table(available_width))
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
    elements.append(Paragraph("Operational Annex — Actionable Cycle Detail", section_style))

    # ---- Qualité des données (contrôle préalable, informatif) ----
    quality_report = compute_data_quality_report(df)
    elements.append(Paragraph(
        f"Data Quality — estimated reliability {quality_report['reliability_pct']}%",
        system_style,
    ))
    elements.append(Paragraph(
        "Preliminary check of the source file's reliability, ahead of the IAM controls "
        "themselves — purely informational, does not alter any data or analysis result.",
        note_style,
    ))
    issue_labels = {
        "username_missing": "Missing account identifiers",
        "duplicate_usernames": "Duplicate accounts (same identifier + system)",
        "invalid_dates": "Unparseable last login dates",
        "future_dates": "Last login dates in the future",
        "unknown_status": "Unrecognized account statuses",
        "system_missing": "System not specified",
        "manager_missing": "Manager not specified",
    }
    quality_rows = [["Indicator", "Value"], ["Rows analyzed", str(quality_report["total_rows"])]]
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
        elements.append(Paragraph("No data quality issues detected in this file.", note_style))
    elements.append(Spacer(1, 0.4 * cm))

    # ---- Résumé exécutif ----
    elements.append(Paragraph("Executive Summary", section_style))
    # 'Total Accounts Reviewed' reste le vrai total de la population
    # revue (acceptés compris) — risk_level est déjà recalculé par
    # apply_risk_acceptances en excluant les constats acceptés, donc la
    # répartition Critique/Élevé/Moyen/Faible est déjà correcte sans
    # filtre supplémentaire ici. Les comptages BRUTS par indicateur
    # (Dormant Accounts, etc.) ci-dessous doivent, eux, exclure
    # spécifiquement les comptes acceptés POUR CE constat précis — voir
    # is_finding_accepted, jamais une exclusion globale du compte.
    from analysis.risk_acceptance import is_finding_accepted

    def _count_excluding_accepted(flag_col: str, control_key: str = None) -> int:
        control_key = control_key or flag_col
        if "accepted_finding_keys" not in df.columns:
            return int(df[flag_col].sum())
        mask = df[flag_col] & ~df.apply(lambda r: is_finding_accepted(r, control_key), axis=1)
        return int(mask.sum())

    df_all_accounts = df
    total_accounts_reviewed = len(df)
    risk_counts = df["risk_level"].value_counts() if "risk_level" in df.columns else {}
    summary_data = [["Indicator", "Value"], ["Total Accounts Reviewed", str(total_accounts_reviewed)]]
    for risk in RISK_COLORS_HEX:
        summary_data.append([_translate_value(risk), str(int(risk_counts.get(risk, 0)))])
    if "is_terminated_but_active" in df.columns:
        summary_data.append(["Active Accounts of Departed Employees", str(_count_excluding_accepted("is_terminated_but_active"))])
    if "is_dormant" in df.columns:
        summary_data.append(["Dormant Accounts", str(_count_excluding_accepted("is_dormant"))])
    if "is_never_used" in df.columns:
        summary_data.append(["Never Used Accounts", str(_count_excluding_accepted("is_never_used"))])
    if "is_password_stale" in df.columns:
        summary_data.append(["Stale Passwords", str(_count_excluding_accepted("is_password_stale"))])
    if "is_privileged_flag" in df.columns and "has_non_expiring_password" in df.columns:
        combined_mask = df["is_privileged_flag"] & df["has_non_expiring_password"]
        if "accepted_finding_keys" in df.columns:
            combined_mask = combined_mask & ~df.apply(
                lambda r: is_finding_accepted(r, "has_non_expiring_password"), axis=1
            )
        summary_data.append(["Privileged Accounts with Non-Expiring Password", str(int(combined_mask.sum()))])
    if "is_duplicate_account" in df.columns:
        summary_data.append(["Duplicate Accounts", str(_count_excluding_accepted("is_duplicate_account"))])
    if "is_locked" in df.columns:
        summary_data.append(["Locked Accounts (outside dormancy)", str(_count_excluding_accepted("is_locked"))])

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

    # ---- Exceptions : comptes dont le risque a été formellement accepté ----
    # Section RÉELLE, alimentée par de vraies données (contrairement à
    # l'ancienne section "Exceptions Report" retirée plus tôt, qui
    # n'était qu'un texte descriptif sans donnée derrière). Ne couvre que
    # le constat précis accepté — voir analysis/risk_acceptance.py.
    elements.append(Paragraph("Exceptions — Risques acceptés", section_style))
    df = df_all_accounts  # restaure l'ensemble complet (voir Executive Summary plus haut)
    accepted_detail = get_accepted_findings_detail(df)
    if accepted_detail:
        exc_labels = ["Account", "System", "Accepted Finding", "Justification", "Accepted By", "Expiration"]
        exc_col_widths = _compute_column_widths(exc_labels, available_width)
        exc_cell_style = ParagraphStyle("ExcCell", fontSize=7.5, leading=9, fontName=DEFAULT_FONT)
        exc_header_style = ParagraphStyle("ExcHeader", fontSize=7.5, leading=9, fontName=DEFAULT_FONT_BOLD, textColor=colors.white)
        header_row = [Paragraph(label, exc_header_style) for label in exc_labels]
        data_rows = [
            [Paragraph(str(d[k] or "—"), exc_cell_style) for k in
             ("username", "system", "accepted_finding", "comment", "accepted_by", "expiration_date")]
            for d in accepted_detail
        ]
        exc_table = Table([header_row] + data_rows, colWidths=exc_col_widths, repeatRows=1)
        exc_table.setStyle(TableStyle([
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
        elements.append(exc_table)
    else:
        elements.append(Paragraph("No risk acceptance recorded for this cycle.", note_style))
    # Acceptations expirées : redevenues des findings actifs, mais
    # signalées ici distinctement pour attirer l'attention sur le besoin
    # de revalidation plutôt que de se fondre dans les findings normaux.
    has_expired = "expired_finding_keys" in df.columns and df["expired_finding_keys"].apply(len).gt(0).any()
    if has_expired:
        expired_names = df.loc[df["expired_finding_keys"].apply(len).gt(0), "username"].astype(str).tolist()
        elements.append(Paragraph(
            f"⚠ {len(expired_names)} account(s) had a risk acceptance that has EXPIRED and "
            f"require re-validation (now listed as active findings again): {', '.join(expired_names)}.",
            note_style,
        ))
    elements.append(Spacer(1, 0.3 * cm))

    elements.append(Paragraph("Annexes", section_style))

    # Sections A à F laissées vides à la demande explicite de l'utilisateur
    # (équipe MTN) : seuls les titres sont générés, le contenu est ajouté
    # manuellement après coup — pas de tableau ni de donnée pré-remplie.
    for letter, title in [
        ("A", "User access form of created accounts"),
        ("B", "Justification of Reactivated accounts"),
        ("C", "Rationale for Profile Change"),
        ("D", "List Of Users Used for the review"),
        ("F", "First List user access review Report"),
    ]:
        elements.append(Paragraph(f"{letter}. {title}", system_style))
        elements.append(Spacer(1, 1.5 * cm))

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
