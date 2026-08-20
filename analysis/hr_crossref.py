"""
Module de croisement IAM + RH.

Corrige une limite structurelle identifiée sur les exports LDAP/AD purs :
un annuaire ne contient jamais le statut RH réel d'un employé (parti ou
non), puisque cette information vit dans le SIRH, pas dans l'annuaire.

Ce module prend un export IAM (comptes/accès) et un export RH (source de
vérité sur qui est actuellement employé), et enrichit le premier avec le
statut RH réel du second, avant de lancer l'analyse habituelle.

Sans ce croisement, la détection "employé parti mais compte actif" est
strictement impossible sur un export IAM qui ne contient pas nativement
le statut RH (cas de LDIF/LDAP, par exemple).
"""

from __future__ import annotations
import logging

import pandas as pd

logger = logging.getLogger("hr_crossref")

# Colonnes attendues côté RH — un référentiel de mapping dédié, plus léger
# que celui des accès puisque le besoin est plus restreint.
HR_COLUMN_MAPPING = {
    "hr_username": [
        "username", "user_id", "login", "identifiant", "matricule",
        "employee_id", "sam_account_name", "employee_number",
    ],
    "hr_employee_status": [
        "employee_status", "statut_employe", "hr_status", "statut_rh",
        "employment_status", "statut",
    ],
    "hr_department": [
        "department", "departement", "département", "service",
    ],
}

HR_REQUIRED_FIELDS = ["hr_username"]


def cross_reference_with_hr(iam_df: pd.DataFrame, hr_df_raw_path: str = None, hr_df: pd.DataFrame = None) -> pd.DataFrame:
    """
    Enrichit iam_df avec le statut RH réel provenant d'un export RH, en les
    rapprochant par nom d'utilisateur (colonne 'username' côté IAM).

    Accepte soit un chemin de fichier RH (hr_df_raw_path, lu et standardisé
    via le moteur d'ingestion générique — tous formats supportés), soit un
    DataFrame RH déjà standardisé (hr_df, pour un usage programmatique).

    Si un compte IAM n'a pas de correspondance dans le référentiel RH, son
    statut est marqué 'Inconnu (absent du référentiel RH)' plutôt que
    d'être silencieusement ignoré — c'est en soi une anomalie à vérifier
    (compte IAM sans employé RH correspondant = potentiel compte fantôme
    ou prestataire externe non déclaré).

    Le statut RH réel (hr_employee_status) écrase 'employee_status' si ce
    dernier était déjà présent côté IAM — la source RH fait autorité.
    """
    if "username" not in iam_df.columns:
        raise ValueError("Le DataFrame IAM doit contenir une colonne 'username'.")

    if hr_df is None:
        if hr_df_raw_path is None:
            raise ValueError("Fournir soit hr_df_raw_path, soit hr_df.")
        from ingestion.ingest import load_file_with_mapping
        hr_df = load_file_with_mapping(hr_df_raw_path, HR_COLUMN_MAPPING, HR_REQUIRED_FIELDS)

    hr_lookup = hr_df.drop_duplicates(subset="hr_username").set_index("hr_username")

    iam_df = iam_df.copy()
    matched_status = iam_df["username"].map(
        hr_lookup["hr_employee_status"] if "hr_employee_status" in hr_lookup.columns else {}
    )

    n_unmatched = matched_status.isna().sum()
    if n_unmatched > 0:
        logger.warning(
            f"{n_unmatched} compte(s) IAM sans correspondance dans le "
            "référentiel RH — statut marqué comme inconnu, à vérifier "
            "manuellement (compte externe/prestataire non déclaré ?)."
        )

    iam_df["employee_status"] = matched_status.fillna("Inconnu (absent du référentiel RH)")
    iam_df["hr_cross_referenced"] = True

    if "hr_department" in hr_lookup.columns:
        iam_df["department"] = iam_df["username"].map(hr_lookup["hr_department"]).fillna(
            iam_df.get("department")
        )

    logger.info(
        f"Croisement RH terminé : {len(iam_df) - n_unmatched}/{len(iam_df)} "
        "comptes rapprochés avec succès."
    )

    return iam_df


if __name__ == "__main__":
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent.parent))

    logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")

    from ingestion.ingest import load_file
    from analysis.access_review import analyze_access, summarize

    if len(sys.argv) < 3:
        print("Usage : python -m analysis.hr_crossref <export_iam> <export_rh>")
        sys.exit(1)

    iam_data = load_file(sys.argv[1])

    enriched = cross_reference_with_hr(iam_data, hr_df_raw_path=sys.argv[2])
    result = analyze_access(enriched)
    print(summarize(result))
