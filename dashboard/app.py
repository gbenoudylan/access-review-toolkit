"""
Dashboard Streamlit — Access Review & IAM Anomaly Detection Toolkit.

Lancement :
    streamlit run dashboard/app.py
"""

from __future__ import annotations
import logging
import sys
import io
import zipfile
import tempfile
from datetime import datetime
from io import BytesIO
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd
import streamlit as st

from ingestion.ingest import load_file, load_file_with_mapping, IngestionError, compute_data_quality_report
from ingestion.custom_column_mappings import (
    load_custom_column_mappings, save_custom_column_mapping, forget_custom_column_mapping,
    DEFAULT_STORE_PATH as MAIN_CUSTOM_MAPPING_STORE_PATH,
)
from ingestion.custom_status_mappings import (
    load_custom_status_mappings, save_custom_status_mapping, forget_custom_status_mapping,
)
from ingestion.custom_role_mappings import (
    load_custom_role_mappings, save_custom_role_mapping, forget_custom_role_mapping,
)
from ingestion.custom_rights_mappings import (
    load_custom_rights_mappings, save_custom_rights_mapping, forget_custom_rights_mapping,
    get_all_distinct_rights_from_df,
)
from analysis.access_review import analyze_access, summarize
from analysis.hr_crossref import (
    cross_reference_with_hr, load_transferred_employees, flag_transferred_but_still_active,
    HR_COLUMN_MAPPING, HR_REQUIRED_FIELDS, TransferNameColumnNotFoundError,
)
from analysis.sod_detection import detect_sod_conflicts, load_custom_sod_matrix
from analysis.trend_tracking import record_cycle_snapshot, load_trend_history
from analysis.risk_acceptance import (
    save_risk_acceptance, remove_risk_acceptance, apply_risk_acceptances,
    ACCEPTABLE_FINDING_KEYS, get_accepted_findings_detail,
)
from analysis.review_workflow import (
    attach_review_status, review_summary, apply_review_decision, VALID_STATUSES, get_audit_trail,
)
from reporting.export import generate_excel_report, generate_pdf_report, generate_word_report, compute_control_coverage, default_report_filename

st.set_page_config(page_title="Access Review Toolkit", page_icon="🔐", layout="wide")

RISK_ORDER = ["Critique", "Élevé", "Moyen", "Faible"]
DECISIONS_STORE_PATH = Path(__file__).parent.parent / "data" / "review_decisions.json"
RISK_ACCEPTANCE_STORE_PATH = Path(__file__).parent.parent / "data" / "risk_acceptances.json"
STATUS_CUSTOM_MAPPING_STORE_PATH = Path(__file__).parent.parent / "data" / "custom_status_mappings.json"
ROLE_CUSTOM_MAPPING_STORE_PATH = Path(__file__).parent.parent / "data" / "custom_role_mappings.json"
RIGHTS_CUSTOM_MAPPING_STORE_PATH = Path(__file__).parent.parent / "data" / "custom_rights_mappings.json"
# Magasin SÉPARÉ de celui de l'export d'accès principal : les champs
# standard visés diffèrent entièrement (hr_username, hr_employee_status...
# vs last_login_date, account_status...) — une même colonne source
# pourrait légitimement correspondre à des champs différents selon le
# fichier d'où elle vient ; les mélanger ferait courir le risque qu'une
# correction apprise pour l'un s'applique à tort à l'autre.
HR_CUSTOM_MAPPING_STORE_PATH = Path(__file__).parent.parent / "data" / "custom_hr_column_mappings.json"
# Magasin séparé pour le fichier de mouvements RH (transferts/mutations) —
# champs visés (transfer_full_name, transfer_old_department,
# transfer_new_department) propres à ce domaine, distincts des deux autres.
TRANSFER_CUSTOM_MAPPING_STORE_PATH = Path(__file__).parent.parent / "data" / "custom_transfer_column_mappings.json"
TREND_STORE_PATH = Path(__file__).parent.parent / "data" / "trend_history.json"


@st.cache_data(show_spinner=False)
def run_pipeline(
    file_bytes: bytes, filename: str,
    hr_file_bytes: bytes = None, hr_filename: str = None,
    default_system: str = None,
    dormant_threshold_days: int = 90,
    password_stale_threshold_days: int = 90,
    never_used_threshold_days: int = 30,
    sod_conflicts: tuple = None,
    extraction_date: str = None,
    transfer_file_bytes: bytes = None, transfer_filename: str = None,
    transfer_sheet_name: str = None,
    terminated_file_bytes: bytes = None, terminated_filename: str = None,
) -> tuple[pd.DataFrame, list, list, dict, dict, list]:
    suffix = Path(filename).suffix
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(file_bytes)
        tmp_path = tmp.name
    # Correspondances de colonnes apprises manuellement lors d'une
    # session précédente (voir section "Colonnes non reconnues" plus
    # bas) : chargées à chaque analyse pour que l'outil reste utilisable
    # sans intervention même quand les noms de colonnes changent d'un
    # export à l'autre.
    custom_mappings = load_custom_column_mappings()
    try:
        df = load_file(
            tmp_path, default_system=default_system or None, custom_mappings=custom_mappings,
            raise_on_missing_required=False,
        )
    except IngestionError as e:
        pass
        empty_df = pd.DataFrame()
        empty_df.attrs["ingestion_error"] = str(e)
        empty_df.attrs["unmapped_columns"] = []
        empty_df.attrs["full_column_mapping"] = {}
        empty_df.attrs["missing_required_fields"] = []
        empty_df.attrs["unknown_status_values"] = []
        return empty_df, [], [], {}, {}, []

    # Garantie absolue : index unique avant toute opération —
    # certains exports Excel produisent des index dupliqués selon
    # la façon dont pandas lit le fichier (header détecté hors ligne 0,
    # multi-feuilles, etc.). Cette protection est en plus de celle
    # d'analyze_access pour couvrir tout le pipeline.
    if not df.index.is_unique:
        df = df.reset_index(drop=True)
    unmapped_columns = list(df.attrs.get("unmapped_columns", []))
    full_column_mapping = dict(df.attrs.get("full_column_mapping", {}))
    missing_required = list(df.attrs.get("missing_required_fields") or [])
    if missing_required:
        # Un champ obligatoire (ex. 'username') reste introuvable même
        # après reconnaissance automatique — possiblement parce
        # qu'AUCUNE colonne du fichier n'a pu être reconnue. Plutôt que
        # de planter (comme c'était le cas avant), on s'arrête ici :
        # l'appelant (dashboard) détecte missing_required_fields et
        # affiche l'interface de correction des colonnes plutôt que de
        # tenter une analyse impossible sans identifiant.
        df.attrs["missing_required_fields"] = missing_required
        return df, unmapped_columns, [], full_column_mapping, {}, []

    if hr_file_bytes is not None:
        hr_suffix = Path(hr_filename).suffix
        with tempfile.NamedTemporaryFile(suffix=hr_suffix, delete=False) as hr_tmp:
            hr_tmp.write(hr_file_bytes)
            hr_tmp_path = hr_tmp.name
        # Chargé séparément (plutôt que de laisser cross_reference_with_hr
        # le faire lui-même) pour pouvoir capturer ses propres colonnes
        # non reconnues et appliquer les mêmes correspondances apprises
        # que pour l'export d'accès principal — magasin séparé, voir
        # HR_CUSTOM_MAPPING_STORE_PATH.
        hr_custom_mappings = load_custom_column_mappings(store_path=HR_CUSTOM_MAPPING_STORE_PATH)
        try:
            hr_df_raw = load_file_with_mapping(
                hr_tmp_path, HR_COLUMN_MAPPING, HR_REQUIRED_FIELDS, custom_mappings=hr_custom_mappings,
            )
            hr_unmapped_columns = list(hr_df_raw.attrs.get("unmapped_columns", []))
            hr_full_column_mapping = dict(hr_df_raw.attrs.get("full_column_mapping", {}))
            df = cross_reference_with_hr(df, hr_df=hr_df_raw)
        except IngestionError as e:
            hr_unmapped_columns = []
            hr_full_column_mapping = {}
            # L'erreur sera affichée dans le dashboard via hr_ingestion_error
            logging.getLogger("dashboard").warning(f"Fichier RH ignoré (illisible) : {e}")
            df.attrs["hr_ingestion_error"] = str(e)
    else:
        hr_unmapped_columns = []
        hr_full_column_mapping = {}

    # Date d'extraction comme référence pour tous les calculs d'ancienneté :
    # une revue peut porter sur un fichier extrait il y a plusieurs semaines,
    # pas nécessairement aujourd'hui — sans ce paramètre, un compte inactif
    # depuis l'extraction serait sous-évalué (comparé à "aujourd'hui" plutôt
    # qu'à la vraie date de la photo des données).
    reference_dt = datetime.strptime(extraction_date, "%Y-%m-%d") if extraction_date else None

    status_custom_mappings = load_custom_status_mappings(store_path=STATUS_CUSTOM_MAPPING_STORE_PATH)
    role_custom_mappings = load_custom_role_mappings(store_path=ROLE_CUSTOM_MAPPING_STORE_PATH)
    rights_custom_mappings = load_custom_rights_mappings(store_path=RIGHTS_CUSTOM_MAPPING_STORE_PATH)

    df = analyze_access(
        df,
        dormant_threshold_days=dormant_threshold_days,
        password_stale_threshold_days=password_stale_threshold_days,
        never_used_threshold_days=never_used_threshold_days,
        reference_datetime=reference_dt,
        custom_status_mappings=status_custom_mappings if status_custom_mappings else None,
        custom_rights_mappings=rights_custom_mappings if rights_custom_mappings else None,
    )
    df = detect_sod_conflicts(
        df, conflicts=sod_conflicts,
        custom_role_mappings=role_custom_mappings if role_custom_mappings else None,
    )

    if transfer_file_bytes is not None:
        transfer_suffix = Path(transfer_filename).suffix
        with tempfile.NamedTemporaryFile(suffix=transfer_suffix, delete=False) as transfer_tmp:
            transfer_tmp.write(transfer_file_bytes)
            transfer_tmp_path = transfer_tmp.name
        try:
            transfer_custom_mappings = load_custom_column_mappings(store_path=TRANSFER_CUSTOM_MAPPING_STORE_PATH)
            transferred = load_transferred_employees(
                transfer_tmp_path, sheet_name=transfer_sheet_name or None, custom_mappings=transfer_custom_mappings,
            )
            df = flag_transferred_but_still_active(df, transferred)
            df.attrs["_transfer_data_present"] = True
        except (ValueError, KeyError, IngestionError) as e:
            logging.getLogger("dashboard").warning(f"Fichier de mutations ignoré : {e}")
            df.attrs["transfer_ingestion_error"] = str(e)

    # Cross-référence avec la liste des employés partis (Terminated)
    # Même logique que les transferts mais sans structure de feuilles —
    # un fichier simple avec une colonne de noms et une date de départ.
    if terminated_file_bytes is not None:
        terminated_suffix = Path(terminated_filename).suffix
        with tempfile.NamedTemporaryFile(suffix=terminated_suffix, delete=False) as term_tmp:
            term_tmp.write(terminated_file_bytes)
            term_tmp_path = term_tmp.name
        try:
            from ingestion.ingest import load_file as _lf
            # Passer default_system="HR" pour éviter que le nom du fichier
            # temporaire (tmp2t9h_xu9) ne s'affiche comme système dans la section 18
            terminated_raw = _lf(term_tmp_path, default_system="HR", raise_on_missing_required=False)
            # Supprimer la colonne system artificielle (non pertinente pour la liste RH)
            if "system" in terminated_raw.columns:
                terminated_raw = terminated_raw.drop(columns=["system"])
            # Chercher une colonne "name" / "nom" / "full_name" dans le fichier terminated
            name_col = None
            for col in terminated_raw.columns:
                if any(k in str(col).lower() for k in ("name","nom","prenom","full")):
                    name_col = col
                    break
            if name_col and not terminated_raw.empty:
                from analysis.hr_crossref import _names_match as _nm
                hr_names = terminated_raw[name_col].dropna().astype(str).str.strip().tolist()
                hr_names = [n for n in hr_names if n and n.lower() not in ("nan","none","")]
                if hr_names:
                    from analysis.access_review import _is_active_account as _iaa
                    is_active_mask = (
                        df["account_status"].apply(_iaa)
                        if "account_status" in df.columns
                        else pd.Series(True, index=df.index)
                    )

                    def _any_match(iam_name: str) -> bool:
                        if not iam_name or not iam_name.strip():
                            return False
                        return any(_nm(iam_name, hr) for hr in hr_names)

                    # Comparer contre full_name ET username (au cas où le fichier
                    # n'a pas de colonne Nom séparée — ex. Linux/BSS où full_name
                    # = username comme "tinaudN", "blekpyee"...)
                    match_by_name = df["full_name"].fillna("").astype(str).apply(_any_match) \
                        if "full_name" in df.columns else pd.Series(False, index=df.index)
                    match_by_user = df["username"].fillna("").astype(str).apply(_any_match) \
                        if "username" in df.columns else pd.Series(False, index=df.index)
                    is_terminated_match = match_by_name | match_by_user

                    df["is_terminated_but_active"] = (
                        df.get("is_terminated_but_active", pd.Series(False, index=df.index))
                        | (is_terminated_match & is_active_mask)
                    )
                    df.attrs["_hr_data_present"] = True
                    n = int((is_terminated_match & is_active_mask).sum())
                    if n:
                        logging.getLogger("dashboard").info(
                            f"{n} compte(s) actif(s) d'employés partis détecté(s) "
                            "(matching nom + username avec initiales)."
                        )
        except Exception as e:
            logging.getLogger("dashboard").warning(f"Fichier terminated ignoré : {e}")

    # Combiner terminated + transferred dans is_terminated_but_active
    if "is_transferred_but_active" in df.columns:
        existing = df.get("is_terminated_but_active", pd.Series(False, index=df.index))
        df["is_terminated_but_active"] = existing | df["is_transferred_but_active"]

    unknown_status_values = list(df.attrs.get("unknown_status_values", []))
    return df, unmapped_columns, hr_unmapped_columns, full_column_mapping, hr_full_column_mapping, unknown_status_values


@st.cache_data(show_spinner=False)
def _check_transfer_file_columns(file_bytes: bytes, filename: str, sheet_name: str, custom_mappings: dict):
    """Vérifie les colonnes reconnues d'un fichier de transferts SANS
    refaire l'analyse complète — mis en cache (comme run_pipeline) pour
    ne pas reparser le fichier à chaque interaction du dashboard sans
    rapport (changer un seuil, taper dans un champ...), Streamlit
    réexécutant tout le script à chaque interaction."""
    suffix = Path(filename).suffix
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(file_bytes)
        tmp_path = tmp.name
    try:
        result = load_transferred_employees(tmp_path, sheet_name=sheet_name or None, custom_mappings=custom_mappings)
        raw_columns = result.attrs.get("raw_columns", [])
        matched = result.attrs.get("matched_columns", [])
        full_mapping = dict(result.attrs.get("full_column_mapping", {}))
        # Dédupliquer les colonnes — un fichier avec deux tableaux empilés
        # dans la même feuille peut avoir le même nom de colonne deux fois
        seen = set()
        raw_columns_dedup = []
        for col in raw_columns:
            key = str(col).strip().lower()
            if key and key not in ("nan","none","") and col not in seen:
                seen.add(col)
                raw_columns_dedup.append(col)
        return raw_columns_dedup, [c for c in raw_columns_dedup if c not in matched], True, None, full_mapping
    except TransferNameColumnNotFoundError as e:
        return list(e.raw_columns), list(e.raw_columns), False, None, {}
    except ValueError as e:
        return [], [], True, str(e), {}


@st.cache_data(show_spinner=False)
def _check_previous_file_columns(file_bytes: bytes, filename: str, custom_mappings: dict):
    """Même principe que _check_transfer_file_columns : mis en cache
    pour ne pas reparser le fichier de revue précédente à chaque
    interaction du dashboard sans rapport."""
    suffix = Path(filename).suffix
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(file_bytes)
        tmp_path = tmp.name
    try:
        df = load_file(
            tmp_path, default_system=None, custom_mappings=custom_mappings,
            raise_on_missing_required=False,
        )
        ingestion_error = df.attrs.get("ingestion_error")
        if ingestion_error:
            return [], ingestion_error, {}
        return list(df.attrs.get("unmapped_columns", [])), None, dict(df.attrs.get("full_column_mapping", {}))
    except IngestionError as e:
        return [], str(e), {}


def _render_column_mapping_ui(
    title: str, raw_columns: list, full_mapping: dict, standard_fields: list,
    store_path, key_prefix: str, missing_required: list = None,
    important_missing: list = None, important_empty: list = None,
    support_inversion: bool = False,
) -> None:
    """
    Interface UNIQUE et fusionnée pour la correspondance des colonnes
    d'un fichier — remplace ce qui était auparavant deux sections
    séparées ("colonnes non reconnues" et "voir toutes les
    correspondances"), source de confusion. Montre TOUJOURS l'ensemble
    des colonnes du fichier avec leur correspondance actuelle
    (« Ignorée » si aucune), modifiable pour n'importe laquelle — pas
    seulement celles en échec, puisqu'une colonne reconnue
    automatiquement peut l'être À TORT sans qu'aucune alerte ne se
    déclenche.

    Dépliée par défaut si quelque chose d'important nécessite
    attention (champ obligatoire manquant, champ important absent ou
    vide) ; repliée sinon, pour ne jamais imposer d'étape à chaque
    import quand la détection automatique s'est bien passée.
    """
    # Plus de garde "if not raw_columns: return" — on affiche TOUJOURS
    # la section, même vide, pour que l'utilisateur voie qu'elle existe.
    missing_required = missing_required or []
    important_missing = important_missing or []
    important_empty = important_empty or []

    with st.expander(
        f"Correspondances de colonnes ({len(raw_columns)}) — {title}",
        expanded=True,
    ):
        if not raw_columns:
            st.caption("Aucune colonne détectée dans ce fichier.")
            return
        if missing_required:
            st.error(
                "Champ(s) indispensable(s) introuvable(s), même après reconnaissance "
                "automatique — l'analyse ne peut pas continuer sans eux : "
                + ", ".join(missing_required) + ". Associe la bonne colonne ci-dessous."
            )
        if important_missing:
            st.warning("Champs importants absents : " + ", ".join(important_missing))
        if important_empty:
            st.warning(
                "Champs importants présents mais entièrement VIDES (une autre colonne du "
                "fichier contient peut-être la vraie donnée, ex. un indicateur "
                "vrai/faux comme 'identity/accountDisabled' au lieu de 'status') : "
                + ", ".join(important_empty)
            )
        st.caption(
            "Chaque colonne du fichier et le champ auquel elle correspond. Change "
            "n'importe laquelle si elle te semble incorrecte, même déjà reconnue "
            "automatiquement — la correction est mémorisée pour tout prochain fichier "
            "portant ce même nom de colonne, sans qu'il soit nécessaire de la refaire."
        )
        options = ["Ignorée"] + standard_fields
        review_assignments, invert_choices = {}, {}
        seen_col_keys = set()
        for ci, col in enumerate(raw_columns):
            col_str = str(col).strip()
            if not col_str or col_str.lower() in ("nan", "none", ""):
                continue
            safe_col_key = f"{key_prefix}_map_{ci}_{col_str[:30]}"
            if safe_col_key in seen_col_keys:
                continue
            seen_col_keys.add(safe_col_key)
            current = full_mapping.get(col)
            default_idx = options.index(current) if current in options else 0
            choice = st.selectbox(
                f"'{col_str}' correspond à :", options=options, index=default_idx,
                key=safe_col_key,
            )
            if choice != (current or "Ignorée"):
                review_assignments[col] = choice
            if support_inversion and choice == "account_status":
                invert_choices[col] = st.checkbox(
                    f"'{col_str}' est un indicateur inversé (ex. 'accountDisabled' : "
                    f"vrai = compte désactivé, PAS actif) plutôt qu'un statut direct",
                    key=f"{key_prefix}_invert_{ci}_{col_str[:30]}",
                )
        if review_assignments and st.button("Enregistrer ces correspondances et relancer l'analyse", key=f"{key_prefix}_map_save"):
            for raw_col, standard_field in review_assignments.items():
                if standard_field == "Ignorée":
                    forget_custom_column_mapping(raw_col, store_path=store_path)
                else:
                    target = (
                        f"{standard_field}__inverted_bool"
                        if invert_choices.get(raw_col) else standard_field
                    )
                    save_custom_column_mapping(raw_col, target, store_path=store_path)
            st.cache_data.clear()
            st.success(f"{len(review_assignments)} correspondance(s) mise(s) à jour. Relance en cours...")
            st.rerun()


def main():
    st.title("Access Review & IAM Anomaly Detection Toolkit")
    st.caption(
        "Ingestion universelle · Détection des comptes orphelins, dormants "
        "et privilèges non justifiés · Plan de revue priorisé"
    )

    with st.sidebar:
        with st.expander("💾 Sauvegarde des données de l'outil"):
            st.caption(
                "Tout ce que l'outil a appris ou enregistré (décisions de revue, "
                "correspondances de colonnes apprises, acceptations de risque, "
                "historique de tendance) vit dans quelques fichiers sur cette "
                "machine — rien n'est envoyé ailleurs. Télécharge une sauvegarde "
                "de temps en temps, surtout avant de changer de machine : sans "
                "elle, tout redémarrerait de zéro."
            )
            data_dir = Path(__file__).parent.parent / "data"
            data_files = sorted(data_dir.glob("*.json")) if data_dir.exists() else []
            if data_files:
                backup_buffer = io.BytesIO()
                with zipfile.ZipFile(backup_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
                    for f in data_files:
                        zf.write(f, arcname=f.name)
                st.download_button(
                    f"Télécharger une sauvegarde ({len(data_files)} fichier(s))",
                    data=backup_buffer.getvalue(),
                    file_name=f"sauvegarde_access_review_{datetime.now().strftime('%Y%m%d')}.zip",
                    mime="application/zip",
                )
            else:
                st.caption("Rien à sauvegarder pour l'instant — aucune donnée enregistrée.")

        st.header("📁 Import")
        uploaded_file = st.file_uploader(
            "Export d'accès (tous formats supportés)",
            type=["csv", "xlsx", "xls", "docx", "txt", "json", "xml",
                  "html", "htm", "ldif", "pdf", "jpeg", "jpg", "png", "zip"],
            help="CSV, Excel, Word, texte libre, JSON, XML, HTML, LDIF "
                 "(export LDAP/AD), PDF, ou une archive ZIP contenant "
                 "plusieurs de ces fichiers.",
        )
        default_system = st.text_input(
            "Nom du système (si absent du fichier)",
            placeholder="ex. Active Directory, SIEM, CRM...",
            help="Certains exports bruts (ex. extraction AD pure) ne "
                 "précisent pas eux-mêmes de quel système ils viennent. "
                 "Renseigne un nom ici s'il manque — sinon, le nom du "
                 "fichier sera utilisé par défaut.",
        )
        extraction_date = st.date_input(
            "Date d'extraction de ce fichier", value=datetime.now().date(),
            help="Si ce fichier n'a pas été extrait aujourd'hui (revue d'une "
                 "extraction plus ancienne), indique la vraie date ici — tous "
                 "les calculs d'ancienneté (dormance, mot de passe, comptes "
                 "créés récemment...) sont faits depuis CETTE date, pas "
                 "depuis aujourd'hui.",
        )
        use_sample = False
        if uploaded_file is None:
            use_sample = st.checkbox("Utiliser un fichier d'exemple", value=True)

        # ── Comptes partis (Terminated) ──────────────────────────────────────
        st.divider()
        st.subheader("🚪 Employés partis — Terminated (optionnel)")
        terminated_uploaded_file = st.file_uploader(
            "Liste des employés dont le contrat est terminé",
            type=["xlsx", "xls", "csv"],
            key="terminated_upload",
            help="Fichier RH listant les employés partis cette année — utilisé dans le contrôle 18A.",
        )

        # ── Comptes transférés/mutés ─────────────────────────────────────────
        st.divider()
        st.subheader("🔄 Employés transférés/mutés — Transferred (optionnel)")
        transfer_uploaded_file = st.file_uploader(
            "Fichier RH de mouvements (feuille Affectation/Mutation)",
            type=["xlsx", "xls"],
            key="transferred_upload",
            help="Classeur RH multi-feuilles : seule la feuille "
                 "mutation/affectation est utilisée.",
        )
        transfer_sheet_name = None
        if transfer_uploaded_file is not None:
            transfer_sheet_name = st.text_input(
                "Nom exact de la feuille (si non détecté automatiquement)",
                placeholder="ex. Affectation-Mutation 2026",
                help="Laisser vide : la première feuille dont le nom contient "
                     "'affectation', 'mutation' ou 'transfert' est utilisée.",
            )
            transfer_custom_mappings_check = load_custom_column_mappings(store_path=TRANSFER_CUSTOM_MAPPING_STORE_PATH)
            transfer_raw_columns, transfer_still_unmapped, transfer_name_found, transfer_check_error, transfer_full_mapping = (
                _check_transfer_file_columns(
                    transfer_uploaded_file.getvalue(), transfer_uploaded_file.name,
                    transfer_sheet_name or "", transfer_custom_mappings_check,
                )
            )
            if transfer_check_error:
                st.error(
                    f"**Fichier de mouvements illisible** — il sera ignoré. "
                    f"Cause : {transfer_check_error}"
                )
            else:
                TRANSFER_STANDARD_FIELDS = [
                    "transfer_full_name", "transfer_old_department", "transfer_new_department",
                ]
                _render_column_mapping_ui(
                    "fichier de transferts", list(transfer_full_mapping.keys()) + transfer_still_unmapped,
                    transfer_full_mapping, TRANSFER_STANDARD_FIELDS, TRANSFER_CUSTOM_MAPPING_STORE_PATH, "transfer",
                    missing_required=[] if transfer_name_found else ["transfer_full_name"],
                )

        # ── RH et SoD : fonctionnalités conservées, non exposées pour l'instant ──
        # Les modules analysis/hr_crossref.py et analysis/sod_detection.py
        # restent intacts et appelables depuis le code — le dashboard ne les
        # propose pas pour simplifier l'interface dans un premier temps.
        hr_uploaded_file = None          # pas de section RH dans l'UI
        sod_conflicts = None             # pas de section SoD dans l'UI

        # ── Seuils des contrôles ─────────────────────────────────────────────
        st.divider()
        st.subheader("⚙️ Seuils des contrôles")
        dormant_threshold_days = st.number_input(
            "Seuil de dormance (jours)", min_value=1, value=90, step=15,
            help="Un compte est considéré 'dormant' sans connexion depuis plus de ce nombre de jours.",
        )
        password_stale_threshold_days = st.number_input(
            "Seuil d'ancienneté du mot de passe (jours)", min_value=1, value=90, step=15,
            help="Un mot de passe est considéré périmé au-delà de ce nombre de jours.",
        )
        never_used_threshold_days = st.number_input(
            "Seuil 'jamais utilisé' (jours depuis création)", min_value=1, value=30, step=5,
            help="Un compte jamais connecté n'est signalé qu'après ce délai depuis sa création.",
        )

    df, error = None, None
    try:
        if uploaded_file is not None:
            with st.spinner("Traitement du fichier..."):
                hr_bytes = hr_uploaded_file.getvalue() if hr_uploaded_file else None
                hr_name = hr_uploaded_file.name if hr_uploaded_file else None
                transfer_bytes = transfer_uploaded_file.getvalue() if transfer_uploaded_file else None
                transfer_name = transfer_uploaded_file.name if transfer_uploaded_file else None
                terminated_bytes = terminated_uploaded_file.getvalue() if terminated_uploaded_file else None
                terminated_name = terminated_uploaded_file.name if terminated_uploaded_file else None
                df, unmapped_columns, hr_unmapped_columns, full_column_mapping, hr_full_column_mapping, unknown_status_values = run_pipeline(
                    uploaded_file.getvalue(), uploaded_file.name, hr_bytes, hr_name,
                    default_system=default_system,
                    dormant_threshold_days=dormant_threshold_days,
                    password_stale_threshold_days=password_stale_threshold_days,
                    never_used_threshold_days=never_used_threshold_days,
                    sod_conflicts=tuple(sod_conflicts) if sod_conflicts else None,
                    extraction_date=extraction_date.strftime('%Y-%m-%d'),
                    transfer_file_bytes=transfer_bytes, transfer_filename=transfer_name,
                    transfer_sheet_name=transfer_sheet_name or None,
                    terminated_file_bytes=terminated_bytes, terminated_filename=terminated_name,
                )
        elif use_sample:
            sample_path = Path(__file__).parent.parent / "data" / "export_test_A.csv"
            with st.spinner("Traitement du fichier d'exemple..."):
                df, unmapped_columns, hr_unmapped_columns, full_column_mapping, hr_full_column_mapping, unknown_status_values = run_pipeline(
                    sample_path.read_bytes(), sample_path.name,
                    dormant_threshold_days=dormant_threshold_days,
                    password_stale_threshold_days=password_stale_threshold_days,
                    never_used_threshold_days=never_used_threshold_days,
                    sod_conflicts=tuple(sod_conflicts) if sod_conflicts else None,
                    extraction_date=extraction_date.strftime('%Y-%m-%d'),
                )
    except IngestionError as e:
        error = f"Erreur d'ingestion : {e}"
    except Exception as e:
        error = f"Erreur inattendue : {e}"

    if error:
        st.error(error)
        st.info("Vérifiez que le fichier contient au minimum : identifiant du compte, système.")
        return
    if df is None:
        st.info("⬅️ Importez un fichier ou cochez 'Utiliser un fichier d'exemple' pour commencer.")
        return

    ingestion_error = df.attrs.get("ingestion_error") if hasattr(df, "attrs") else None
    if ingestion_error:
        st.error(
            f"**Le fichier n'a pas pu être lu.** Cause : {ingestion_error}\n\n"
            "Vérifie que le fichier n'est pas vide, qu'il contient au moins "
            "une ligne d'en-tête et une ligne de données, et qu'il est dans "
            "un format supporté (CSV, Excel, Word, PDF, LDIF...)."
        )
        return

    # Recentré sur les champs qui pilotent réellement un contrôle (ou
    # sont indispensables à l'identification) — demande explicite de ne
    # pas noyer le menu avec des champs purement informatifs (nom
    # complet, email, téléphone, poste...) qu'aucun contrôle n'utilise
    # directement.
    STANDARD_FIELDS_FOR_MAPPING = [
        "username", "system", "account_status", "is_locked",
        "last_login_date", "account_created_date", "password_last_set",
        # user_rights : droits réels octroyés (USER RIGHTS/PERMISSIONS Oracle EBS,
        # entitlements, permissions...) — inclut la détection automatique des admins
        "user_rights",
        "description",
    ]
    missing_required = list(df.attrs.get("missing_required_fields") or [])
    # full_column_mapping vient du tuple de retour de run_pipeline.
    # Colonnes internes produites par analyze_access — jamais à configurer
    _INTERNAL_COLS = frozenset({
        "days_since_last_login","last_login_date_convention_uncertain",
        "last_login_date_convention_status","last_login_future","is_dormant",
        "last_login_date_unparseable","days_since_creation","is_never_used",
        "is_recently_created","status_is_unknown","is_active_for_audit",
        "is_terminated_but_active","is_transferred_but_active",
        "is_privileged_flag","has_no_manager","days_since_password_change",
        "password_change_future","password_change_unknown","is_password_stale",
        "has_non_expiring_password","is_service_account","is_test_account",
        "is_orphaned_account","is_non_compliant_naming","is_duplicate_account",
        "temporal_inconsistency","review_action","risk_level","risk_score",
        "risk_score_reasons","sod_conflict","sod_conflict_detail",
        "transferred_name_ambiguous","accepted_finding_keys","hr_cross_referenced",
    })

    if not full_column_mapping and not unmapped_columns:
        # Fallback : reconstituer depuis les colonnes du df analysé,
        # en excluant les colonnes internes (dérivées par analyze_access)
        full_column_mapping = {
            col: col for col in df.columns
            if col not in ("system",) and col not in _INTERNAL_COLS
        }
    else:
        # Filtrer aussi les colonnes internes du mapping si elles s'y sont glissées
        full_column_mapping = {
            k: v for k, v in full_column_mapping.items()
            if k not in _INTERNAL_COLS
        }
    # Filtrer les colonnes sans nom valide (NaN, vides) — elles génèrent
    # des clés de widgets dupliquées dans Streamlit ("main_map_nan")
    def _is_valid_col(c) -> bool:
        s = str(c).strip()
        return bool(s) and s.lower() not in ("nan", "none", "")
    all_main_raw_columns = [c for c in list(full_column_mapping.keys()) + [
        c for c in unmapped_columns if c not in full_column_mapping
    ] if _is_valid_col(c)]
    if not all_main_raw_columns:
        all_main_raw_columns = [c for c in df.columns if _is_valid_col(c)]

    if missing_required:
        # Un champ indispensable (ex. 'username') reste introuvable même
        # après reconnaissance automatique — possiblement parce qu'AUCUNE
        # colonne du fichier n'a pu être reconnue. Corrige un vrai défaut
        # signalé : ceci plantait auparavant plutôt que de laisser
        # l'utilisateur associer les colonnes lui-même. On s'arrête ici,
        # avant toute tentative d'analyse impossible sans identifiant.
        _render_column_mapping_ui(
            "fichier principal", all_main_raw_columns, full_column_mapping,
            STANDARD_FIELDS_FOR_MAPPING, MAIN_CUSTOM_MAPPING_STORE_PATH, "main",
            missing_required=missing_required, support_inversion=True,
        )
        return

    def _is_effectively_empty(series) -> bool:
        return series.isna().all() or (series.astype(str).str.strip().isin(["", "nan", "none"])).all()

    important_check_fields = ("last_login_date", "password_last_set", "account_status")
    important_missing = [f for f in important_check_fields if f not in df.columns]
    important_empty = [
        f for f in important_check_fields
        if f in df.columns and f not in important_missing and _is_effectively_empty(df[f])
    ]

    # L'interface de configuration complète (colonnes + statuts + droits)
    # est maintenant dans la section unifiée ci-dessous.
    # On conserve _render_column_mapping_ui uniquement pour la revue
    # précédente et le fichier de transferts.

    # ── Section unifiée : Configuration du fichier ──────────────────────────
    # Trois onglets dans un seul expander — colonnes, statuts, droits —
    # plus une section "Déjà enregistré" unique qui regroupe tout ce qui
    # a été appris manuellement lors des sessions précédentes.
    all_status_learned  = load_custom_status_mappings(store_path=STATUS_CUSTOM_MAPPING_STORE_PATH)
    all_status_in_file  = df.attrs.get("all_status_values", {}) if hasattr(df, "attrs") else {}
    unknown_status_values_this_run = unknown_status_values
    all_distinct_rights = get_all_distinct_rights_from_df(df) if df is not None and not df.empty else []
    rights_learned      = load_custom_rights_mappings(store_path=RIGHTS_CUSTOM_MAPPING_STORE_PATH)

    has_unknown_status  = bool(unknown_status_values_this_run)
    n_cols   = len(all_main_raw_columns)
    n_status = len(all_status_in_file)
    n_rights = len(all_distinct_rights)

    with st.expander(
        f"{'⚠️ ' if (missing_required or has_unknown_status) else ''}Configuration du fichier"
        f" — {n_cols} colonnes · {n_status} statuts · {n_rights} droits",
        expanded=bool(missing_required or important_missing or important_empty or has_unknown_status),
    ):
        tab_cols, tab_status, tab_rights = st.tabs([
            f"Colonnes ({n_cols})",
            f"Statuts ({n_status})" + (" ⚠️" if has_unknown_status else ""),
            f"Droits ({n_rights})",
        ])

        # ── Onglet 1 : Colonnes ───────────────────────────────────────────
        with tab_cols:
            if missing_required:
                st.error(
                    "Champ(s) indispensable(s) introuvable(s) — l'analyse ne peut "
                    "pas continuer sans eux : " + ", ".join(missing_required) +
                    ". Associe la bonne colonne ci-dessous."
                )
            if important_missing:
                st.warning("Champs importants absents : " + ", ".join(important_missing))
            if important_empty:
                st.warning("Champs présents mais entièrement vides : " + ", ".join(important_empty))

            st.caption(
                "Associe chaque colonne au champ qu'elle représente. "
                "La correction est mémorisée pour tous les prochains fichiers portant ce même nom de colonne."
            )
            options_cols = ["Ignorée"] + STANDARD_FIELDS_FOR_MAPPING
            col_assignments = {}
            invert_choices  = {}
            seen_keys = set()
            for i, col in enumerate(all_main_raw_columns):
                # Ignorer les colonnes sans nom (NaN, vides) — elles n'ont rien à mapper
                col_str = str(col).strip()
                if not col_str or col_str.lower() in ("nan", "none", ""):
                    continue
                # Clé basée sur la POSITION (i) + nom — garantit l'unicité même
                # si deux colonnes portent le même nom (ce qui peut arriver avec
                # des exports Excel mal formés ou des colonnes dupliquées)
                safe_key = f"main_map_{i}_{col_str}"
                if safe_key in seen_keys:
                    continue
                seen_keys.add(safe_key)
                current = full_column_mapping.get(col)
                default_idx = options_cols.index(current) if current in options_cols else 0
                choice = st.selectbox(
                    f"'{col_str}' correspond à :",
                    options=options_cols, index=default_idx,
                    key=safe_key,
                )
                if choice != (current or "Ignorée"):
                    col_assignments[col] = choice
                if choice == "account_status":
                    invert_choices[col] = st.checkbox(
                        f"'{col_str}' est un indicateur inversé (ex. accountDisabled : vrai = désactivé)",
                        key=f"main_invert_{i}_{col_str}",
                    )
            if col_assignments and st.button("Enregistrer colonnes et relancer", key="main_col_save"):
                for raw_col, standard_field in col_assignments.items():
                    if standard_field == "Ignorée":
                        forget_custom_column_mapping(raw_col, store_path=MAIN_CUSTOM_MAPPING_STORE_PATH)
                    else:
                        target = f"{standard_field}__inverted_bool" if invert_choices.get(raw_col) else standard_field
                        save_custom_column_mapping(raw_col, target, store_path=MAIN_CUSTOM_MAPPING_STORE_PATH)
                st.cache_data.clear()
                st.success(f"{len(col_assignments)} correspondance(s) enregistrée(s). Relance en cours...")
                st.rerun()

        # ── Onglet 2 : Statuts ────────────────────────────────────────────
        with tab_status:
            if has_unknown_status:
                st.warning(
                    f"Valeur(s) non reconnue(s) — traitée(s) comme actives par défaut "
                    f"(pire cas audit) : {', '.join(unknown_status_values_this_run)}. "
                    "Corrige ci-dessous pour affiner."
                )
            if all_status_in_file:
                st.caption("Toutes les valeurs de statut du fichier avec leur interprétation actuelle. Corrige si nécessaire.")
                status_assignments = {}
                seen_status_keys = set()
                for j, (val, auto_label) in enumerate(all_status_in_file.items()):
                    val_str = str(val).strip()
                    if not val_str or val_str.lower() in ("nan", "none", ""):
                        continue
                    safe_status_key = f"status_sel_{j}_{val_str[:30]}"
                    if safe_status_key in seen_status_keys:
                        continue
                    seen_status_keys.add(safe_status_key)
                    learned = all_status_learned.get(val_str)
                    if learned == "active":
                        display = "Actif (corrigé)"
                    elif learned == "inactive":
                        display = "Inactif (corrigé)"
                    else:
                        display = auto_label.replace("🟢 ", "").replace("🔴 ", "").replace("🔒 ", "").replace("⚠️ ", "")
                    options_s = [f"Garder ({display})", "Actif", "Disabled"]
                    choice = st.selectbox(
                        f"'{val_str}'",
                        options=options_s, index=0,
                        key=safe_status_key,
                    )
                    if choice == "Actif":
                        status_assignments[val] = "active"
                    elif choice == "Disabled":
                        status_assignments[val] = "inactive"
                if status_assignments and st.button("Enregistrer statuts et relancer", key="status_save_all"):
                    for raw_val, target in status_assignments.items():
                        save_custom_status_mapping(raw_val, target, store_path=STATUS_CUSTOM_MAPPING_STORE_PATH)
                    st.cache_data.clear()
                    st.success(f"{len(status_assignments)} valeur(s) enregistrée(s). Relance en cours...")
                    st.rerun()
            else:
                st.caption("Aucune valeur de statut dans ce fichier.")

        # ── Onglet 3 : Droits ─────────────────────────────────────────────
        with tab_rights:
            from analysis.access_review import _is_privileged_role_value
            if all_distinct_rights:
                n_admin_total = sum(
                    1 for r in all_distinct_rights
                    if rights_learned.get(r) == "admin" or
                    (rights_learned.get(r) != "standard" and _is_privileged_role_value(r))
                )
                st.caption(
                    f"{n_admin_total} droit(s) classé(s) Admin sur {n_rights}. "
                    "Les droits détectés automatiquement comme Admin sont pré-sélectionnés. "
                    "Corrige manuellement ceux que l'outil ne reconnaît pas."
                )
                rights_assignments = {}
                seen_rights_keys = set()
                for k, right in enumerate(all_distinct_rights):
                    right_str = str(right).strip()
                    if not right_str or right_str.lower() in ("nan", "none", ""):
                        continue
                    safe_right_key = f"right_sel_{k}_{right_str[:30]}"
                    if safe_right_key in seen_rights_keys:
                        continue
                    seen_rights_keys.add(safe_right_key)
                    if rights_learned.get(right) == "admin":
                        current_cat, is_auto = "Admin", False
                    elif rights_learned.get(right) == "standard":
                        current_cat, is_auto = "Standard", False
                    elif _is_privileged_role_value(right):
                        current_cat, is_auto = "Admin", True
                    else:
                        current_cat, is_auto = "Non qualifié", False
                    label = right_str + (" *(auto)*" if is_auto else "")
                    options_r = ["Admin", "Standard", "Non qualifié"]
                    idx = options_r.index(current_cat) if current_cat in options_r else 2
                    new_cat = st.selectbox(label, options=options_r, index=idx, key=safe_right_key)
                    if not is_auto and new_cat != current_cat:
                        if new_cat == "Admin":
                            rights_assignments[right] = "admin"
                        elif new_cat == "Standard":
                            rights_assignments[right] = "standard"
                        else:
                            rights_assignments[right] = None

                if st.button("Enregistrer droits et relancer", key="rights_save"):
                    for right, cat in rights_assignments.items():
                        if cat is None:
                            forget_custom_rights_mapping(right, store_path=RIGHTS_CUSTOM_MAPPING_STORE_PATH)
                        else:
                            save_custom_rights_mapping(right, cat, store_path=RIGHTS_CUSTOM_MAPPING_STORE_PATH)
                    st.cache_data.clear()
                    st.success(f"{len(rights_assignments)} qualification(s) enregistrée(s). Relance en cours...")
                    st.rerun()
            else:
                st.caption("Aucun droit trouvé dans ce fichier (colonne user_rights / role absente).")

    # ── Section unifiée : Déjà enregistré ───────────────────────────────────
    all_learned          = load_custom_column_mappings()
    transfer_all_learned = load_custom_column_mappings(store_path=TRANSFER_CUSTOM_MAPPING_STORE_PATH)
    total_learned = len(all_learned) + len(transfer_all_learned) + len(all_status_learned) + len(rights_learned)

    if total_learned > 0:
        with st.expander(f"Corrections déjà enregistrées ({total_learned}) — cliquer pour modifier"):
            if all_learned:
                st.markdown("**Colonnes (fichier principal)**")
                for raw_col, target in list(all_learned.items()):
                    disp = target.replace("__inverted_bool", " (inversé)")
                    c1, c2 = st.columns([4, 1])
                    c1.write(f"{raw_col} → {disp}")
                    if c2.button("Retirer", key=f"forget_{raw_col}"):
                        forget_custom_column_mapping(raw_col)
                        st.cache_data.clear(); st.rerun()
            if transfer_all_learned:
                st.markdown("**Colonnes (transferts)**")
                for raw_col, target in list(transfer_all_learned.items()):
                    c1, c2 = st.columns([4, 1])
                    c1.write(f"{raw_col} → {target}")
                    if c2.button("Retirer", key=f"xfer_forget_{raw_col}"):
                        forget_custom_column_mapping(raw_col, store_path=TRANSFER_CUSTOM_MAPPING_STORE_PATH)
                        st.cache_data.clear(); st.rerun()
            if all_status_learned:
                st.markdown("**Statuts**")
                for raw_val, target in list(all_status_learned.items()):
                    c1, c2 = st.columns([4, 1])
                    c1.write(f"{raw_val} → {"Active" if target == "active" else "Disabled"}")
                    if c2.button("Retirer", key=f"status_forget_{raw_val}"):
                        forget_custom_status_mapping(raw_val, store_path=STATUS_CUSTOM_MAPPING_STORE_PATH)
                        st.cache_data.clear(); st.rerun()
            if rights_learned:
                st.markdown("**Droits**")
                for right, cat in list(rights_learned.items()):
                    c1, c2 = st.columns([4, 1])
                    c1.write(f"{right} → {cat}")
                    if c2.button("Retirer", key=f"rights_forget_{right}"):
                        forget_custom_rights_mapping(right, store_path=RIGHTS_CUSTOM_MAPPING_STORE_PATH)
                        st.cache_data.clear(); st.rerun()

    # Rôles SoD masqués (interface non exposée pour l'instant).
    # Section RH masquée (interface non exposée pour l'instant).

    df = apply_risk_acceptances(df, store_path=RISK_ACCEPTANCE_STORE_PATH)
    df = attach_review_status(df, store_path=DECISIONS_STORE_PATH)
    summary = summarize(df)
    workflow_summary = review_summary(df)

    quality_report = compute_data_quality_report(df)
    with st.expander(
        f"Qualité des données — fiabilité {quality_report['reliability_pct']}%",
        expanded=quality_report["reliability_pct"] < 90,
    ):
        st.caption(
            "Vérification préalable, avant les contrôles IAM eux-mêmes : "
            "purement informatif, ne bloque et ne modifie rien."
        )
        issue_labels = {
            "username_missing": "Identifiants de compte manquants",
            "duplicate_usernames": "Comptes en doublon (même identifiant + système)",
            "invalid_dates": "Dates de dernière connexion non interprétables",
            "unknown_status": "Statuts de compte non reconnus",
            "system_missing": "Système non renseigné",
            "manager_missing": "Manager non renseigné",
        }
        qcol1, qcol2 = st.columns(2)
        with qcol1:
            st.metric("Lignes analysées", quality_report["total_rows"])
        with qcol2:
            st.metric("Fiabilité estimée", f"{quality_report['reliability_pct']}%")
        for key, label in issue_labels.items():
            count = quality_report["issues"].get(key)
            if count:
                st.warning(f"{label} : {count}")
        if summary.get("date_convention_uncertain"):
            st.warning(
                "Convention jour/mois pour la dernière connexion devinée par défaut "
                "(aucune valeur de la colonne ne permet de trancher entre JJ/MM et MM/JJ) — "
                "à vérifier si le fichier provient d'un système utilisant une autre convention."
            )
        if summary.get("temporal_inconsistencies"):
            st.warning(
                f"{summary['temporal_inconsistencies']} compte(s) avec une incohérence "
                f"temporelle (connexion ou changement de mot de passe antérieur à la "
                f"date de création du compte)."
            )
        if summary.get("future_dates"):
            st.warning(
                f"{summary['future_dates']} compte(s) avec une date de connexion ou de "
                f"mot de passe dans le futur — probable anomalie de données à la source."
            )

    # filtered est calculé ici (avant Rapports formatés et Détail des comptes)
    # pour être disponible dans les deux sections — les filtres de risque
    # s'appliquent aussi bien aux rapports qu'au tableau de détail.
    filtered = df  # par défaut : tous les comptes

    st.subheader("Rapports formatés")

    period_label = st.text_input(
        "Période couverte par ce rapport",
        placeholder="ex. T1 2026, Mars 2026...",
        help="Laisser vide pour utiliser automatiquement le trimestre courant. "
             "Ce champ permet de relancer ce même rapport à chaque cycle de revue "
             "sans modifier le code.",
    )

    with st.expander("Comparer avec la revue précédente — optionnel"):
        previous_file = st.file_uploader(
            "Revue précédente (même format que l'export d'accès)",
            type=["csv", "xlsx", "xls", "docx", "txt", "json", "xml", "html", "htm", "ldif", "pdf", "jpeg", "jpg", "png", "zip"],
            key="previous_review_upload",
            help="Fournis le fichier de la revue précédente pour que le rapport calcule "
                 "automatiquement les comptes créés, supprimés, réactivés et les profils "
                 "modifiés entre les deux cycles.",
        )
        previous_extraction_date = st.date_input(
            "Date d'extraction de cette revue précédente", value=None,
            help="Utilisée dans les tableaux de comparaison (Profile Modified, Reactivated "
                 "accounts) pour dater précisément l'ancienne valeur, à côté de la nouvelle.",
            key="previous_extraction_date_input",
        )

        # Même correction manuelle que pour le fichier d'accès principal —
        # même magasin de correspondances (même format de fichier), pour
        # qu'une correction apprise sur l'un s'applique aussi à l'autre.
        # Vérifié dès l'upload plutôt qu'au moment de générer le rapport :
        # sans ça, une colonne non reconnue ne se remarquerait qu'après
        # avoir cliqué sur "Générer", trop tard pour corriger sereinement.
        if previous_file is not None:
            prev_check_mappings = load_custom_column_mappings()
            prev_check_unmapped, prev_check_error, prev_full_mapping = _check_previous_file_columns(
                previous_file.getvalue(), previous_file.name, prev_check_mappings,
            )
            if prev_check_error:
                st.error(
                    f"**Revue précédente illisible** — la comparaison entre cycles ne sera pas disponible. "
                    f"Cause : {prev_check_error}"
                )
            else:
                _render_column_mapping_ui(
                    "revue précédente", list(prev_full_mapping.keys()) + prev_check_unmapped,
                    prev_full_mapping, STANDARD_FIELDS_FOR_MAPPING, MAIN_CUSTOM_MAPPING_STORE_PATH, "prevreview",
                    support_inversion=True,
                )

    with st.expander("En-tête du document officiel — optionnel"):
        header_col1, header_col2 = st.columns(2)
        with header_col1:
            department = st.text_input("Département émetteur", placeholder="ex. Technology Department")
            application_scope = st.text_input("Périmètre / Application", placeholder="ex. Active Directory")
            extraction_origin = st.text_input(
                "Origine de l'extraction (nom du fichier)", placeholder="ex. Extraction ServiceNow mensuelle",
                help="Remplace le nom de système déduit automatiquement dans le nom du fichier "
                     "téléchargé. Laissé vide, le système est repris automatiquement comme avant.",
            )
        with header_col2:
            editor = st.text_input("Éditeur du document", placeholder="Nom, Prénom")
            document_version = st.text_input("Version du document", value="1.0")
        include_controls_reference = st.checkbox(
            "Inclure le référentiel des 18 contrôles standards", value=True,
        )
        logo_file = st.file_uploader(
            "Logo de l'entreprise (optionnel — utilise assets/mtnlogo.png par défaut si présent)",
            type=["png", "jpg", "jpeg"],
            help="Un logo importé ici remplace ponctuellement celui par défaut, pour ce "
                 "rapport uniquement.",
        )

    with st.expander("Validation (sign-off) — optionnel"):
        signoff_col1, signoff_col2, signoff_col3 = st.columns(3)
        with signoff_col1:
            prepared_by = st.text_input("Préparé par", placeholder="Nom, Prénom")
        with signoff_col2:
            reviewed_by = st.text_input("Revu par", placeholder="Nom, Prénom")
        with signoff_col3:
            approved_by = st.text_input("Approuvé par", placeholder="Nom, Prénom")

    report_col1, report_col2, report_col3 = st.columns(3)
    with report_col1:
        if st.button("Générer le rapport Excel", use_container_width=True):
            with st.spinner("Génération..."):
                tmp_xlsx = Path(tempfile.gettempdir()) / "rapport_revue_acces.xlsx"
                generate_excel_report(filtered, tmp_xlsx)
                buf = BytesIO(tmp_xlsx.read_bytes())
            st.download_button(
                "Télécharger le rapport Excel", data=buf.getvalue(),
                file_name=default_report_filename(filtered, "xlsx", extraction_origin=extraction_origin),
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True,
            )

    def _resolve_previous_df_and_logo():
        previous_df = None
        previous_unmapped = []
        if previous_file is not None:
            prev_suffix = Path(previous_file.name).suffix
            with tempfile.NamedTemporaryFile(suffix=prev_suffix, delete=False) as tmp_prev:
                tmp_prev.write(previous_file.getvalue())
                tmp_prev_path = tmp_prev.name
            try:
                # Même format que le fichier d'accès principal (un export
                # antérieur du même système) — réutilise le même magasin de
                # correspondances apprises, pas un magasin séparé : une
                # correction apprise sur l'un doit s'appliquer à l'autre.
                previous_custom_mappings = load_custom_column_mappings()
                # Passer le même système par défaut que la revue courante —
                # évite que l'ancienne revue hérite d'un nom de système
                # bizarre déduit de son nom de fichier, ce qui fausse la
                # comparaison entre cycles (système "Extraction_prev" vs
                # "Oracle EBS" par exemple).
                current_system = (
                    df["system"].dropna().astype(str).str.strip().mode()[0]
                    if "system" in df.columns and not df["system"].isna().all()
                    else default_system or None
                )
                previous_raw = load_file(
                    tmp_prev_path, default_system=current_system,
                    custom_mappings=previous_custom_mappings,
                )
                previous_unmapped = list(previous_raw.attrs.get("unmapped_columns", []))
                if not previous_raw.index.is_unique:
                    previous_raw = previous_raw.reset_index(drop=True)
                previous_df = analyze_access(
                    previous_raw,
                    dormant_threshold_days=dormant_threshold_days,
                    password_stale_threshold_days=password_stale_threshold_days,
                    never_used_threshold_days=never_used_threshold_days,
                )
            except IngestionError as e:
                st.warning(f"Revue précédente ignorée (erreur d'ingestion) : {e}")

        logo_path = None
        if logo_file is not None:
            logo_suffix = Path(logo_file.name).suffix
            with tempfile.NamedTemporaryFile(suffix=logo_suffix, delete=False) as tmp_logo:
                tmp_logo.write(logo_file.getvalue())
                logo_path = tmp_logo.name
        else:
            default_logo = Path(__file__).parent.parent / "assets" / "mtnlogo.png"
            if default_logo.exists():
                logo_path = str(default_logo)
        return previous_df, logo_path, previous_unmapped

    def _load_hr_df(uploaded_file_obj):
        """Charge un fichier RH (terminated ou transferred) en DataFrame brut."""
        if uploaded_file_obj is None:
            return None
        try:
            suffix = Path(uploaded_file_obj.name).suffix
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                tmp.write(uploaded_file_obj.getvalue())
                path = tmp.name
            df_hr = load_file(path, raise_on_missing_required=False)
            return df_hr if len(df_hr) > 0 else None
        except Exception:
            return None

    with report_col2:
        if st.button("Générer le rapport PDF", use_container_width=True):
            with st.spinner("Génération..."):
                previous_df, logo_path, _ = _resolve_previous_df_and_logo()
                terminated_df_report = _load_hr_df(terminated_uploaded_file)
                transferred_df_report = _load_hr_df(transfer_uploaded_file)
                tmp_pdf = Path(tempfile.gettempdir()) / "rapport_revue_acces.pdf"
                generate_pdf_report(
                    filtered, tmp_pdf, period=period_label or None,
                    prepared_by=prepared_by or None,
                    reviewed_by=reviewed_by or None,
                    approved_by=approved_by or None,
                    department=department or None,
                    editor=editor or None,
                    application_scope=application_scope or None,
                    document_version=document_version or "1.0",
                    include_controls_reference=include_controls_reference,
                    previous_df=previous_df,
                    logo_path=logo_path,
                    dormant_threshold_days=dormant_threshold_days,
                    current_extraction_date=extraction_date.strftime("%Y-%m-%d"),
                    previous_extraction_date=(
                        previous_extraction_date.strftime("%Y-%m-%d") if previous_extraction_date else None
                    ),
                    terminated_df=terminated_df_report,
                    transferred_df=transferred_df_report,
                )
                buf = BytesIO(tmp_pdf.read_bytes())
            st.download_button(
                "Télécharger le rapport PDF", data=buf.getvalue(),
                file_name=default_report_filename(filtered, "pdf", extraction_origin=extraction_origin), mime="application/pdf",
                use_container_width=True,
            )
    with report_col3:
        if st.button("Générer le rapport Word", use_container_width=True):
            with st.spinner("Génération..."):
                previous_df, logo_path, _ = _resolve_previous_df_and_logo()
                terminated_df_report = _load_hr_df(terminated_uploaded_file)
                transferred_df_report = _load_hr_df(transfer_uploaded_file)
                tmp_docx = Path(tempfile.gettempdir()) / "rapport_revue_acces.docx"
                generate_word_report(
                    filtered, tmp_docx, period=period_label or None,
                    prepared_by=prepared_by or None,
                    reviewed_by=reviewed_by or None,
                    approved_by=approved_by or None,
                    department=department or None,
                    editor=editor or None,
                    application_scope=application_scope or None,
                    document_version=document_version or "1.0",
                    previous_df=previous_df,
                    logo_path=logo_path,
                    dormant_threshold_days=dormant_threshold_days,
                    current_extraction_date=extraction_date.strftime("%Y-%m-%d"),
                    previous_extraction_date=(
                        previous_extraction_date.strftime("%Y-%m-%d") if previous_extraction_date else None
                    ),
                    terminated_df=terminated_df_report,
                    transferred_df=transferred_df_report,
                )
                buf = BytesIO(tmp_docx.read_bytes())
            st.download_button(
                "Télécharger le rapport Word", data=buf.getvalue(),
                file_name=default_report_filename(filtered, "docx", extraction_origin=extraction_origin),
                mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                use_container_width=True,
            )


    st.divider()

    st.subheader("Vue d'ensemble")
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Comptes analysés", summary["total_accounts"])
    col2.metric("Comptes dormants", summary["dormant_accounts"])
    col3.metric("Revue traitée", f"{workflow_summary.get('taux_traitement', 0)}%")
    col4.metric("MDP périmé / non-expirant", summary.get("password_stale", 0))

    st.divider()

    st.subheader("Répartition par niveau de risque")
    vc = df["risk_level"].value_counts() if "risk_level" in df.columns else pd.Series(dtype=int)
    risk_counts = pd.Series({r: int(vc.get(r, 0)) for r in RISK_ORDER})
    st.bar_chart(risk_counts)

    coverage = compute_control_coverage(df, {})
    n_ok = sum(1 for _, _, status, _ in coverage if status == "OK")
    n_warn = sum(1 for _, _, status, _ in coverage if status == "⚠️")
    n_na = sum(1 for _, _, status, _ in coverage if status == "N/A")
    with st.expander(f"Control Coverage — {n_ok + n_warn} / {len(coverage)} contrôles exécutés"):
        st.caption(
            f"{n_ok} OK · {n_warn} avec anomalie(s) · {n_na} non applicable (données insuffisantes)."
        )
        coverage_table = [
            {"N°": number, "Contrôle": title, "Résultat": status, "Comptes": count_display}
            for number, title, status, count_display in coverage
        ]
        st.dataframe(coverage_table, width="stretch", hide_index=True)

    st.divider()

    st.subheader("Détail des comptes")
    filter_col1, filter_col2 = st.columns(2)
    with filter_col1:
        selected_risks = st.multiselect("Filtrer par risque", options=RISK_ORDER, default=RISK_ORDER)
    with filter_col2:
        show_action_needed_only = st.checkbox("Actions requises uniquement", value=False)

    filtered = df[df["risk_level"].isin(selected_risks)]
    if show_action_needed_only and "review_action" in df.columns:
        filtered = filtered[filtered["review_action"] != "Aucune action"]

    display_cols = [
        c for c in [
            "username", "full_name", "department", "system", "manager",
            "account_status", "employee_status", "days_since_last_login",
            "is_privileged_flag", "sod_conflict_detail", "review_action",
            "risk_level", "review_status",
        ] if c in filtered.columns
    ]
    risk_rank = {"Critique": 0, "Élevé": 1, "Moyen": 2, "Faible": 3}
    filtered_sorted = filtered[display_cols].copy()
    filtered_sorted["_rank"] = filtered_sorted["risk_level"].map(risk_rank)
    filtered_sorted = filtered_sorted.sort_values("_rank").drop(columns="_rank")

    st.dataframe(filtered_sorted, width="stretch", hide_index=True)

    st.download_button(
        "Télécharger en CSV",
        data=filtered_sorted.to_csv(index=False).encode("utf-8"),
        file_name="revue_acces.csv",
        mime="text/csv",
    )


    st.divider()

    st.subheader("Investigation de compte")
    st.caption(
        "Sélectionne un compte pour voir sa fiche complète — identité, accès, activité, "
        "risque détaillé et historique complet des décisions de revue."
    )
    if "username" not in df.columns:
        st.info("Colonne 'username' absente : investigation de compte indisponible.")
    else:
        usernames_available = sorted(df["username"].dropna().unique().tolist())
        if not usernames_available:
            st.info("Aucun compte exploitable dans ce fichier.")
        else:
            selected_username = st.selectbox("Compte à investiguer", options=usernames_available)
            # Comparaison insensible à la casse/espaces : le même identifiant
            # peut apparaître avec une casse différente selon le système
            # source (ex. 'jdupont' sur AD, 'JDupont' sur SAP) — une
            # correspondance stricte ferait manquer à l'investigateur les
            # autres comptes de la même personne, sans même lui montrer le
            # sélecteur multi-système qui les signalerait normalement.
            _selected_norm = str(selected_username).strip().lower()
            matches = df[df["username"].astype(str).str.strip().str.lower() == _selected_norm]
            if "system" in df.columns and matches["system"].nunique() > 1:
                selected_system = st.selectbox(
                    "Ce compte existe sur plusieurs systèmes — lequel ?",
                    options=sorted(matches["system"].dropna().unique().tolist()),
                )
                matches = matches[matches["system"] == selected_system]
            account = matches.iloc[0]

            inv_col1, inv_col2, inv_col3 = st.columns(3)
            with inv_col1:
                st.markdown("**Identity**")
                st.write(f"Username : {account.get('username', '—')}")
                st.write(f"Système : {account.get('system', '—')}")
                st.write(f"Statut compte : {account.get('account_status', '—')}")
                st.write(f"Verrouillé : {'Oui' if account.get('is_locked') else 'Non'}")
                desc = account.get("description") or "—"
                if desc != "—" and len(str(desc)) > 60:
                    desc = str(desc)[:60] + "…"
                st.write(f"Description : {desc}")
            with inv_col2:
                st.markdown("**Droits & Privilèges**")
                st.write(f"Privilégié : {'⚠️ Oui' if account.get('is_privileged_flag') else 'Non'}")
                # Afficher user_rights en priorité (droits réels octroyés),
                # puis role (profil métier) — deux champs distincts dans Oracle EBS
                for field_label, field_key in [("Droits (USER RIGHTS)", "user_rights"), ("Rôle", "role")]:
                    val = account.get(field_key) or ""
                    if val and str(val) not in ("—", "nan", "none", ""):
                        roles_list = [r.strip() for r in str(val).split("\n") if r.strip()]
                        if not roles_list:
                            roles_list = [str(val)]
                        st.markdown(f"*{field_label} :*")
                        for r in roles_list[:5]:
                            st.write(f"  • {r}")
                        if len(roles_list) > 5:
                            st.write(f"  … et {len(roles_list)-5} autre(s)")
            with inv_col3:
                st.markdown("**Activité**")
                days_login = account.get("days_since_last_login")
                st.write(f"Dernière connexion : {int(days_login) if pd.notna(days_login) else 'inconnue'} jour(s)")
                days_pwd = account.get("days_since_password_change")
                st.write(f"Âge du mot de passe : {int(days_pwd) if pd.notna(days_pwd) else 'inconnu'} jour(s)")
                st.write(f"Créé le : {account.get('account_created_date') or '—'}")

            st.markdown("**Findings**")
            # Même liste que ACCEPTABLE_FINDING_KEYS (analysis/risk_acceptance.py)
            # — un finding affiché ici doit toujours être acceptable via
            # le formulaire plus bas, sans dupliquer une seconde liste
            # qui risquerait de diverger avec le temps.
            findings = [label for key, label in ACCEPTABLE_FINDING_KEYS.items() if account.get(key)]
            if findings:
                for f in findings:
                    st.write(f)
            else:
                st.write("Aucune anomalie détectée sur ce compte.")

            if "risk_score" in account:
                risk_level_val = account.get("risk_level", "")
                st.markdown(f"**Risk score : {int(account['risk_score'])}/100 — {risk_level_val}**")
                reasons = account.get("risk_score_reasons") or []
                for label, pts in reasons:
                    st.write(f"+ {pts} — {label}")

            # Acceptation de risque : ne couvre que le constat PRÉCIS
            # accepté (voir analysis/risk_acceptance.py) — jamais le
            # compte dans l'absolu. Un compte peut cumuler plusieurs
            # constats indépendants à la fois (ex. dormant ET conflit
            # SoD) : chacun s'accepte séparément, pour ne jamais en
            # masquer un qui n'a pas été spécifiquement traité.
            st.markdown("**Acceptation de risque**")
            acc_username, acc_system = str(account.get("username")), str(account.get("system"))
            accepted_keys = account.get("accepted_finding_keys") or []
            expired_keys = account.get("expired_finding_keys") or []

            for key in accepted_keys:
                label = ACCEPTABLE_FINDING_KEYS.get(key, key)
                st.success(f"Risque accepté pour « {label} ».")
                if st.button("Retirer cette acceptation", key=f"remove_risk_acc_{acc_username}_{acc_system}_{key}"):
                    remove_risk_acceptance(acc_username, acc_system, key, store_path=RISK_ACCEPTANCE_STORE_PATH)
                    st.cache_data.clear()
                    st.rerun()
            for key in expired_keys:
                label = ACCEPTABLE_FINDING_KEYS.get(key, key)
                st.warning(f"L'acceptation pour « {label} » a expiré — redevenu un finding actif, à revalider.")

            # Constats actuellement vrais sur ce compte, pas déjà couverts
            # par une acceptation active — seuls ceux-là peuvent être
            # acceptés maintenant.
            acceptable_now = [
                key for key in ACCEPTABLE_FINDING_KEYS
                if account.get(key) and key not in accepted_keys
            ]
            if acceptable_now:
                target_key = st.selectbox(
                    "Constat à accepter",
                    options=acceptable_now,
                    format_func=lambda k: ACCEPTABLE_FINDING_KEYS.get(k, k),
                    key=f"target_finding_{acc_username}_{acc_system}",
                )
                # La case à cocher doit être HORS du formulaire : à
                # l'intérieur d'un st.form, Streamlit ne réévalue le
                # script qu'à la soumission, pas à chaque interaction —
                # cocher la case ne ferait donc rien apparaître avant
                # que le formulaire entier soit déjà soumis.
                has_expiration = st.checkbox(
                    "Prévoir une échéance de revalidation",
                    key=f"has_expiration_{acc_username}_{acc_system}_{target_key}",
                )
                with st.form(key=f"risk_acc_form_{acc_username}_{acc_system}_{target_key}"):
                    comment = st.text_area(
                        f"Justification pour accepter « {ACCEPTABLE_FINDING_KEYS.get(target_key, target_key)} » sur ce compte",
                    )
                    expiration = st.date_input("Échéance") if has_expiration else None
                    accepted_by = st.text_input("Accepté par")
                    submitted = st.form_submit_button("Accepter ce risque")
                    if submitted:
                        if not comment.strip():
                            st.error("La justification est requise.")
                        else:
                            save_risk_acceptance(
                                acc_username, acc_system, target_key, comment.strip(),
                                accepted_by.strip() or "—",
                                expiration_date=expiration.isoformat() if expiration else None,
                                store_path=RISK_ACCEPTANCE_STORE_PATH,
                            )
                            st.cache_data.clear()
                            st.rerun()
            elif not accepted_keys and not expired_keys:
                st.caption("Aucun constat en cours sur ce compte — rien à accepter.")

            st.markdown("**Review — historique complet**")
            if "system" in df.columns:
                history = get_audit_trail(
                    str(account.get("username")), str(account.get("system")), store_path=DECISIONS_STORE_PATH,
                )
                if history:
                    for entry in history:
                        st.write(
                            f"{entry.get('date', '?')} — **{entry.get('status', '?')}** "
                            f"(par {entry.get('validated_by') or 'non renseigné'})"
                            + (f" — _{entry.get('comment')}_" if entry.get("comment") else "")
                        )
                else:
                    st.write("Aucune décision enregistrée pour ce compte pour l'instant.")


    st.divider()

    st.subheader("Validation de la revue")
    st.caption(
        "Change le statut de chaque compte, puis clique sur 'Enregistrer les "
        "décisions'. Les décisions sont conservées d'une revue à l'autre."
    )

    editable_cols = ["username", "system", "risk_level", "review_status"]
    editable_cols = [c for c in editable_cols if c in filtered.columns]
    editable_df = filtered[editable_cols].copy().reset_index(drop=True)

    edited_df = st.data_editor(
        editable_df,
        width="stretch",
        hide_index=True,
        disabled=["username", "system", "risk_level"],
        column_config={
            "review_status": st.column_config.SelectboxColumn(
                "Statut de revue", options=VALID_STATUSES, required=True,
            ),
        },
        key="review_editor",
    )

    validated_by = st.text_input("Validé par (ton nom)", value="")

    if st.button("Enregistrer les décisions"):
        n_changes = 0
        for i in range(len(edited_df)):
            original_status = editable_df.loc[i, "review_status"]
            new_status = edited_df.loc[i, "review_status"]
            if new_status != original_status:
                apply_review_decision(
                    username=edited_df.loc[i, "username"],
                    system=edited_df.loc[i, "system"],
                    status=new_status,
                    validated_by=validated_by,
                    store_path=DECISIONS_STORE_PATH,
                )
                n_changes += 1
        if n_changes:
            st.success(f"{n_changes} décision(s) enregistrée(s).")
            st.cache_data.clear()
            st.rerun()
        else:
            st.info("Aucun changement à enregistrer.")


    st.divider()

    st.subheader("Tendance dans le temps")
    st.caption(
        "Historise les indicateurs clés de ce cycle pour suivre l'évolution d'une "
        "revue à l'autre — pas seulement l'état du jour. Enregistrement volontaire : "
        "charger le même fichier plusieurs fois pour tester des seuils n'ajoute rien "
        "à l'historique tant que tu ne cliques pas sur le bouton."
    )

    trend_period = st.text_input(
        "Période de ce cycle (pour l'historique)", value="",
        placeholder="ex. T1 2026, Mars 2026...", key="trend_period_input",
    )
    if st.button("Enregistrer ce cycle dans l'historique de tendance"):
        snapshot = record_cycle_snapshot(
            df, trend_period or datetime.now().strftime("%Y-%m-%d"),
            store_path=TREND_STORE_PATH, recorded_by=validated_by,
        )
        st.success(
            f"Cycle enregistré ({snapshot['date']}, périmètre : "
            f"{', '.join(snapshot['systems']) or 'non renseigné'})."
        )
        st.cache_data.clear()

    trend_history_all = load_trend_history(store_path=TREND_STORE_PATH)
    if trend_history_all.empty:
        st.info("Aucun cycle encore enregistré dans l'historique de tendance.")
    else:
        # Dédoublonnage insensible à la casse : le même système peut
        # apparaître avec une casse différente selon le cycle (variation
        # d'export réaliste) — sans ça, 'AD' et 'ad' apparaîtraient comme
        # deux entrées distinctes dans la liste alors qu'elles désignent
        # le même système et donneraient le même résultat une fois
        # sélectionnées.
        raw_systems = {
            s.strip() for row in trend_history_all["systems"] for s in row.split(",")
            if s.strip() and s.strip() != "Non renseigné"
        }
        seen_norm = {}
        for s in raw_systems:
            seen_norm.setdefault(s.lower(), s)  # garde la première casse rencontrée
        available_systems = sorted(seen_norm.values())
        scope_choice = st.selectbox(
            "Périmètre à afficher",
            options=["Tous systèmes (totaux globaux)"] + available_systems,
            help="Comparer un système précis reste valable même si d'autres systèmes "
                 "ont été ajoutés ou retirés du périmètre entre deux cycles — les "
                 "totaux globaux, eux, mélangent tout le périmètre de chaque cycle.",
        )
        trend_history = (
            load_trend_history(store_path=TREND_STORE_PATH)
            if scope_choice == "Tous systèmes (totaux globaux)"
            else load_trend_history(store_path=TREND_STORE_PATH, system=scope_choice)
        )
        if len(trend_history) < 2:
            st.info("Au moins 2 cycles enregistrés sont nécessaires pour tracer une tendance.")
        else:
            metric_options = {
                "is_dormant": "Comptes dormants", "is_never_used": "Jamais utilisés",
                "is_password_stale": "Mots de passe périmés", "is_duplicate_account": "Doublons",
                "is_locked": "Verrouillés", "is_terminated_but_active": "Partis, accès actif",
                "sod_conflict": "Conflits SoD", "total_accounts": "Total comptes",
            }
            available_metrics = {k: v for k, v in metric_options.items() if k in trend_history.columns}
            selected_metrics = st.multiselect(
                "Indicateurs à afficher", options=list(available_metrics.keys()),
                default=[m for m in ("is_dormant", "total_accounts") if m in available_metrics],
                format_func=lambda k: available_metrics[k],
            )
            if selected_metrics:
                chart_data = trend_history.set_index("period_label")[selected_metrics]
                chart_data.columns = [available_metrics[c] for c in chart_data.columns]
                st.line_chart(chart_data)

            if scope_choice == "Tous systèmes (totaux globaux)":
                scopes_seen = trend_history["systems"].tolist()
                scope_changes = [
                    trend_history.loc[i, "period_label"]
                    for i in range(1, len(scopes_seen)) if scopes_seen[i] != scopes_seen[i - 1]
                ]
                if scope_changes:
                    st.caption(
                        f"Périmètre changé avant : {', '.join(scope_changes)} — une variation "
                        f"autour de ces cycles peut venir d'un système ajouté/retiré, pas "
                        f"forcément d'une vraie évolution."
                    )

            with st.expander("Historique détaillé"):
                st.dataframe(trend_history, width="stretch", hide_index=True)


if __name__ == "__main__":
    main()
