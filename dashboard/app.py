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
from reporting.export import generate_excel_report, generate_pdf_report

st.set_page_config(page_title="Access Review Toolkit", page_icon="🔐", layout="wide")

RISK_ORDER = ["Critique", "Élevé", "Moyen", "Faible"]


@st.cache_data(show_spinner=False)
def run_pipeline(file_bytes: bytes, filename: str) -> pd.DataFrame:
    suffix = Path(filename).suffix
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(file_bytes)
        tmp_path = tmp.name
    df = load_file(tmp_path)
    return analyze_access(df)


def main():
    st.title("🔐 Access Review & IAM Anomaly Detection Toolkit")
    st.caption(
        "Ingestion universelle · Détection des comptes orphelins, dormants "
        "et privilèges non justifiés · Plan de revue priorisé"
    )

    with st.sidebar:
        st.header("📁 Import")
        uploaded_file = st.file_uploader(
            "Export d'accès (CSV ou Excel)",
            type=["csv", "xlsx", "xls"],
            help="N'importe quel format de colonnes est accepté.",
        )
        use_sample = False
        if uploaded_file is None:
            use_sample = st.checkbox("Utiliser un fichier d'exemple", value=True)

        st.divider()
        st.caption(
            "Un compte est considéré 'dormant' sans connexion depuis plus de "
            "90 jours (seuil standard du secteur)."
        )

    df, error = None, None
    try:
        if uploaded_file is not None:
            with st.spinner("Traitement du fichier..."):
                df = run_pipeline(uploaded_file.getvalue(), uploaded_file.name)
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

    summary = summarize(df)

    st.subheader("Vue d'ensemble")
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Comptes analysés", summary["total_accounts"])
    col2.metric("🔴 Employés partis, accès actif", summary["terminated_but_active"])
    col3.metric("Comptes dormants", summary["dormant_accounts"])
    col4.metric("Privilégiés dormants", summary["privileged_dormant"])

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
            "is_privileged_flag", "review_action", "risk_level",
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
    st.subheader("📄 Rapports formatés")

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
                generate_pdf_report(filtered, tmp_pdf)
                buf = BytesIO(tmp_pdf.read_bytes())
            st.download_button(
                "⬇️ Télécharger le rapport PDF", data=buf.getvalue(),
                file_name="rapport_revue_acces.pdf", mime="application/pdf",
                use_container_width=True,
            )


if __name__ == "__main__":
    main()
