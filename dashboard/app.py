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

from ingestion.ingest import load_file, IngestionError
from analysis.access_review import analyze_access, summarize
from analysis.hr_crossref import cross_reference_with_hr
from analysis.sod_detection import detect_sod_conflicts
from analysis.review_workflow import (
    attach_review_status, review_summary, apply_review_decision, VALID_STATUSES,
)
from reporting.export import generate_excel_report, generate_pdf_report

st.set_page_config(page_title="Access Review Toolkit", page_icon="🔐", layout="wide")

RISK_ORDER = ["Critique", "Élevé", "Moyen", "Faible"]
DECISIONS_STORE_PATH = Path(__file__).parent.parent / "data" / "review_decisions.json"


@st.cache_data(show_spinner=False)
def run_pipeline(
    file_bytes: bytes, filename: str,
    hr_file_bytes: bytes = None, hr_filename: str = None,
    default_system: str = None,
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

    df = analyze_access(df)
    df = detect_sod_conflicts(df)
    return df


def main():
    st.title("🔐 Access Review & IAM Anomaly Detection Toolkit")
    st.caption(
        "Ingestion universelle · Détection des comptes orphelins, dormants "
        "et privilèges non justifiés · Plan de revue priorisé"
    )

    with st.sidebar:
        st.header("📁 Import")
        uploaded_file = st.file_uploader(
            "Export d'accès (tous formats supportés)",
            type=["csv", "xlsx", "xls", "docx", "txt", "json", "xml",
                  "html", "htm", "ldif", "pdf", "zip"],
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
                  "html", "htm", "ldif", "pdf", "zip"],
            help="Corrige le statut RH réel des comptes, notamment pour les "
                 "exports LDAP/AD qui ne contiennent pas nativement cette "
                 "information. La source RH fait autorité sur le statut employé.",
        )

        st.divider()
        st.caption(
            "Un compte est considéré 'dormant' sans connexion depuis plus de "
            "90 jours (seuil standard du secteur)."
        )

    df, error = None, None
    try:
        if uploaded_file is not None:
            with st.spinner("Traitement du fichier..."):
                hr_bytes = hr_uploaded_file.getvalue() if hr_uploaded_file else None
                hr_name = hr_uploaded_file.name if hr_uploaded_file else None
                df = run_pipeline(
                    uploaded_file.getvalue(), uploaded_file.name, hr_bytes, hr_name,
                    default_system=default_system,
                )
        elif use_sample:
            sample_path = Path(__file__).parent.parent / "data" / "export_test_A.csv"
            with st.spinner("Traitement du fichier d'exemple..."):
                df = run_pipeline(sample_path.read_bytes(), sample_path.name)
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

    st.subheader("Vue d'ensemble")
    col1, col2, col3, col4, col5, col6 = st.columns(6)
    col1.metric("Comptes analysés", summary["total_accounts"])
    col2.metric("🔴 Employés partis, accès actif", summary["terminated_but_active"])
    col3.metric("Comptes dormants", summary["dormant_accounts"])
    col4.metric("⚠️ Conflits SoD", n_sod_conflicts)
    col5.metric("Traité (revue)", f"{workflow_summary.get('taux_traitement', 0)}%")
    col6.metric("🔑 Privilégiés, MDP n'expire jamais", summary["privileged_non_expiring_password"])

    st.divider()

    st.subheader("Répartition par niveau de risque")
    risk_counts = df["risk_level"].value_counts().reindex(RISK_ORDER, fill_value=0)
    st.bar_chart(risk_counts)

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
        "⬇️ Télécharger en CSV",
        data=filtered_sorted.to_csv(index=False).encode("utf-8"),
        file_name="revue_acces.csv",
        mime="text/csv",
    )

    st.divider()
    st.subheader("✅ Validation de la revue")
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

    if st.button("💾 Enregistrer les décisions"):
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
    st.subheader("📄 Rapports formatés")

    period_label = st.text_input(
        "Période couverte par ce rapport",
        placeholder="ex. T1 2026, Mars 2026...",
        help="Laisser vide pour utiliser automatiquement le trimestre courant. "
             "Ce champ permet de relancer ce même rapport à chaque cycle de revue "
             "sans modifier le code.",
    )

    with st.expander("Validation (sign-off) — optionnel"):
        signoff_col1, signoff_col2, signoff_col3 = st.columns(3)
        with signoff_col1:
            prepared_by = st.text_input("Préparé par", placeholder="Nom, Prénom")
        with signoff_col2:
            reviewed_by = st.text_input("Revu par", placeholder="Nom, Prénom")
        with signoff_col3:
            approved_by = st.text_input("Approuvé par", placeholder="Nom, Prénom")

    report_col1, report_col2 = st.columns(2)
    with report_col1:
        if st.button("Générer le rapport Excel", use_container_width=True):
            with st.spinner("Génération..."):
                tmp_xlsx = Path(tempfile.gettempdir()) / "rapport_revue_acces.xlsx"
                generate_excel_report(filtered, tmp_xlsx)
                buf = BytesIO(tmp_xlsx.read_bytes())
            st.download_button(
                "⬇️ Télécharger le rapport Excel", data=buf.getvalue(),
                file_name="rapport_revue_acces.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True,
            )
    with report_col2:
        if st.button("Générer le rapport PDF", use_container_width=True):
            with st.spinner("Génération..."):
                tmp_pdf = Path(tempfile.gettempdir()) / "rapport_revue_acces.pdf"
                generate_pdf_report(
                    filtered, tmp_pdf, period=period_label or None,
                    prepared_by=prepared_by or None,
                    reviewed_by=reviewed_by or None,
                    approved_by=approved_by or None,
                )
                buf = BytesIO(tmp_pdf.read_bytes())
            st.download_button(
                "⬇️ Télécharger le rapport PDF", data=buf.getvalue(),
                file_name="rapport_revue_acces.pdf", mime="application/pdf",
                use_container_width=True,
            )


if __name__ == "__main__":
    main()
