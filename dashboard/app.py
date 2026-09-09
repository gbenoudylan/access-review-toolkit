"""
Dashboard Streamlit — Access Review & IAM Anomaly Detection Toolkit.

Lancement :
    streamlit run dashboard/app.py
"""

from __future__ import annotations
import sys
import tempfile
from io import BytesIO
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd
import streamlit as st

from ingestion.ingest import load_file, IngestionError, compute_data_quality_report
from analysis.access_review import analyze_access, summarize
from analysis.hr_crossref import cross_reference_with_hr
from analysis.sod_detection import detect_sod_conflicts, load_custom_sod_matrix
from analysis.review_workflow import (
    attach_review_status, review_summary, apply_review_decision, VALID_STATUSES, get_audit_trail,
)
from reporting.export import generate_excel_report, generate_pdf_report, generate_word_report, compute_control_coverage

st.set_page_config(page_title="Access Review Toolkit", page_icon="🔐", layout="wide")

RISK_ORDER = ["Critique", "Élevé", "Moyen", "Faible"]
DECISIONS_STORE_PATH = Path(__file__).parent.parent / "data" / "review_decisions.json"


@st.cache_data(show_spinner=False)
def run_pipeline(
    file_bytes: bytes, filename: str,
    hr_file_bytes: bytes = None, hr_filename: str = None,
    default_system: str = None,
    dormant_threshold_days: int = 90,
    password_stale_threshold_days: int = 90,
    never_used_threshold_days: int = 30,
    sod_conflicts: list = None,
) -> pd.DataFrame:
    suffix = Path(filename).suffix
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(file_bytes)
        tmp_path = tmp.name
    df = load_file(tmp_path, default_system=default_system or None)

    if hr_file_bytes is not None:
        hr_suffix = Path(hr_filename).suffix
        with tempfile.NamedTemporaryFile(suffix=hr_suffix, delete=False) as hr_tmp:
            hr_tmp.write(hr_file_bytes)
            hr_tmp_path = hr_tmp.name
        df = cross_reference_with_hr(df, hr_df_raw_path=hr_tmp_path)

    df = analyze_access(
        df,
        dormant_threshold_days=dormant_threshold_days,
        password_stale_threshold_days=password_stale_threshold_days,
        never_used_threshold_days=never_used_threshold_days,
    )
    df = detect_sod_conflicts(df, conflicts=sod_conflicts)
    return df


def main():
    st.title("Access Review & IAM Anomaly Detection Toolkit")
    st.caption(
        "Ingestion universelle · Détection des comptes orphelins, dormants "
        "et privilèges non justifiés · Plan de revue priorisé"
    )

    with st.sidebar:
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
        use_sample = False
        if uploaded_file is None:
            use_sample = st.checkbox("Utiliser un fichier d'exemple", value=True)

        st.divider()
        st.subheader("🔗 Croisement RH (optionnel)")
        hr_uploaded_file = st.file_uploader(
            "Export RH — source de vérité sur qui est employé",
            type=["csv", "xlsx", "xls", "docx", "txt", "json", "xml",
                  "html", "htm", "ldif", "pdf", "jpeg", "jpg", "png", "zip"],
            help="Corrige le statut RH réel des comptes, notamment pour les "
                 "exports LDAP/AD qui ne contiennent pas nativement cette "
                 "information. La source RH fait autorité sur le statut employé.",
        )

        st.divider()
        st.subheader("⚙️ Seuils des contrôles")
        dormant_threshold_days = st.number_input(
            "Seuil de dormance (jours)", min_value=1, value=90, step=15,
            help="Un compte est considéré 'dormant' sans connexion depuis plus de ce nombre de jours.",
        )
        password_stale_threshold_days = st.number_input(
            "Seuil d'ancienneté du mot de passe (jours)", min_value=1, value=90, step=15,
            help="Un mot de passe est considéré périmé au-delà de ce nombre de jours (comptes de service exclus).",
        )
        never_used_threshold_days = st.number_input(
            "Seuil 'jamais utilisé' (jours depuis création)", min_value=1, value=30, step=5,
            help="Un compte jamais connecté n'est signalé qu'après ce délai depuis sa création "
                 "(laisse le temps à un nouveau compte d'être utilisé pour la première fois).",
        )

        st.divider()
        st.subheader("🔐 Matrice SoD personnalisée (optionnel)")
        sod_matrix_file = st.file_uploader(
            "Fichier à 2 colonnes : rôle 1, rôle 2 (paires incompatibles)",
            type=["csv", "xlsx", "xls"],
            help="Sans fichier, une matrice générique par défaut est utilisée (conflits classiques "
                 "finance/achats/IT). Chaque entreprise a sa propre liste de rôles incompatibles — "
                 "fournissez la vôtre pour l'appliquer sans modifier le code.",
        )
        sod_conflicts = None
        if sod_matrix_file is not None:
            try:
                sod_conflicts = load_custom_sod_matrix(sod_matrix_file.getvalue(), sod_matrix_file.name)
                st.success(f"{len(sod_conflicts)} paire(s) de rôles incompatibles chargée(s).")
            except Exception as e:
                st.warning(f"Matrice SoD ignorée (erreur de lecture) : {e}")

    df, error = None, None
    try:
        if uploaded_file is not None:
            with st.spinner("Traitement du fichier..."):
                hr_bytes = hr_uploaded_file.getvalue() if hr_uploaded_file else None
                hr_name = hr_uploaded_file.name if hr_uploaded_file else None
                df = run_pipeline(
                    uploaded_file.getvalue(), uploaded_file.name, hr_bytes, hr_name,
                    default_system=default_system,
                    dormant_threshold_days=dormant_threshold_days,
                    password_stale_threshold_days=password_stale_threshold_days,
                    never_used_threshold_days=never_used_threshold_days,
                    sod_conflicts=sod_conflicts,
                )
        elif use_sample:
            sample_path = Path(__file__).parent.parent / "data" / "export_test_A.csv"
            with st.spinner("Traitement du fichier d'exemple..."):
                df = run_pipeline(
                    sample_path.read_bytes(), sample_path.name,
                    dormant_threshold_days=dormant_threshold_days,
                    password_stale_threshold_days=password_stale_threshold_days,
                    never_used_threshold_days=never_used_threshold_days,
                    sod_conflicts=sod_conflicts,
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

    df = attach_review_status(df, store_path=DECISIONS_STORE_PATH)
    summary = summarize(df)
    workflow_summary = review_summary(df)
    n_sod_conflicts = int(df["sod_conflict"].sum()) if "sod_conflict" in df.columns else 0

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

    st.subheader("Vue d'ensemble")
    col1, col2, col3, col4, col5, col6 = st.columns(6)
    col1.metric("Comptes analysés", summary["total_accounts"])
    col2.metric("Partis, accès actif", summary["terminated_but_active"])
    col3.metric("Comptes dormants", summary["dormant_accounts"])
    col4.metric("Conflits SoD", n_sod_conflicts)
    col5.metric("Revue traitée", f"{workflow_summary.get('taux_traitement', 0)}%")
    col6.metric("Privilégié, MDP permanent", summary["privileged_non_expiring_password"])

    st.divider()

    st.subheader("Répartition par niveau de risque")
    risk_counts = df["risk_level"].value_counts().reindex(RISK_ORDER, fill_value=0)
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
            matches = df[df["username"] == selected_username]
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
                st.write(f"Nom : {account.get('full_name', '—')}")
                st.write(f"Département : {account.get('department', '—')}")
                st.write(f"Manager : {account.get('manager') or '—'}")
                st.write(f"Statut RH : {account.get('employee_status', '—')}")
            with inv_col2:
                st.markdown("**Access**")
                st.write(f"Système : {account.get('system', '—')}")
                st.write(f"Rôle : {account.get('role', '—')}")
                st.write(f"Privilégié : {'Oui' if account.get('is_privileged_flag') else 'Non'}")
                st.write(f"Statut compte : {account.get('account_status', '—')}")
                st.write(f"Verrouillé : {'Oui' if account.get('is_locked') else 'Non'}")
            with inv_col3:
                st.markdown("**Activity**")
                days_login = account.get("days_since_last_login")
                st.write(f"Dernière connexion : {int(days_login) if pd.notna(days_login) else 'inconnue'} jour(s)")
                days_pwd = account.get("days_since_password_change")
                st.write(f"Âge du mot de passe : {int(days_pwd) if pd.notna(days_pwd) else 'inconnu'} jour(s)")
                st.write(f"Créé le : {account.get('account_created_date') or '—'}")

            st.markdown("**Findings**")
            finding_labels = {
                "is_terminated_but_active": "Employé parti, compte encore actif",
                "is_dormant": "Compte dormant",
                "is_never_used": "Jamais utilisé depuis sa création",
                "is_password_stale": "Mot de passe périmé",
                "has_non_expiring_password": "Mot de passe n'expirant jamais",
                "has_no_manager": "Aucun manager identifié",
                "is_duplicate_account": "Compte en doublon",
                "is_test_account": "Nom évoquant un compte de test",
                "is_non_compliant_naming": "Nom non conforme à la convention",
                "sod_conflict": "Conflit de séparation des tâches (SoD)",
            }
            findings = [label for key, label in finding_labels.items() if account.get(key)]
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

    with st.expander("En-tête du document officiel — optionnel"):
        header_col1, header_col2 = st.columns(2)
        with header_col1:
            department = st.text_input("Département émetteur", placeholder="ex. Technology Department")
            application_scope = st.text_input("Périmètre / Application", placeholder="ex. Active Directory")
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
                file_name="rapport_revue_acces.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True,
            )

    def _resolve_previous_df_and_logo():
        previous_df = None
        if previous_file is not None:
            prev_suffix = Path(previous_file.name).suffix
            with tempfile.NamedTemporaryFile(suffix=prev_suffix, delete=False) as tmp_prev:
                tmp_prev.write(previous_file.getvalue())
                tmp_prev_path = tmp_prev.name
            try:
                previous_raw = load_file(tmp_prev_path, default_system=None)
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
        return previous_df, logo_path

    with report_col2:
        if st.button("Générer le rapport PDF", use_container_width=True):
            with st.spinner("Génération..."):
                previous_df, logo_path = _resolve_previous_df_and_logo()
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
                )
                buf = BytesIO(tmp_pdf.read_bytes())
            st.download_button(
                "Télécharger le rapport PDF", data=buf.getvalue(),
                file_name="rapport_revue_acces.pdf", mime="application/pdf",
                use_container_width=True,
            )
    with report_col3:
        if st.button("Générer le rapport Word", use_container_width=True):
            with st.spinner("Génération..."):
                previous_df, logo_path = _resolve_previous_df_and_logo()
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
                )
                buf = BytesIO(tmp_docx.read_bytes())
            st.download_button(
                "Télécharger le rapport Word", data=buf.getvalue(),
                file_name="rapport_revue_acces.docx",
                mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                use_container_width=True,
            )


if __name__ == "__main__":
    main()
