"""
Module d'analyse de revue des accès (access review).

Détecte les anomalies IAM classiques qu'une revue d'accès périodique doit
identifier :

    1. Comptes d'employés partis, mais encore actifs -> le risque le plus
       critique (accès non révoqué après un départ).
    2. Comptes dormants -> pas de connexion depuis longtemps, candidats à
       la désactivation (principe du least privilege).
    3. Comptes privilégiés dormants -> encore plus critique qu'un compte
       standard dormant, car le niveau d'accès est plus dangereux.
    4. Comptes sans manager/owner identifié -> personne ne peut valider si
       cet accès est toujours légitime lors d'une revue.

Chaque compte reçoit un statut d'action et un niveau de risque, pour
produire une liste priorisée plutôt qu'un simple export brut.
"""

from __future__ import annotations
import logging
import re
from datetime import datetime

import pandas as pd

logger = logging.getLogger("access_review")

DORMANT_THRESHOLD_DAYS = 90  # seuil standard du secteur (souvent 60-90 jours)

ACTIVE_STATUS_VALUES = {"active", "actif", "enabled", "activé", "oui", "yes", "true"}
TERMINATED_STATUS_VALUES = {
    "terminated", "termine", "terminé", "parti", "departed", "left",
    "inactive", "inactif", "resigned", "démissionné",
}
PRIVILEGED_VALUES = {"oui", "yes", "true", "1", "admin", "administrateur"}

# Format "Generalized Time" utilisé par LDAP/Active Directory pour les dates
# (ex. whenChanged, whenCreated) : YYYYMMDDHHMMSS[.f]Z — non reconnu
# automatiquement par le parseur de dates générique de pandas.
_LDAP_GENERALIZED_TIME_RE = re.compile(r"^(\d{14})(\.\d+)?Z?$")


def _is_active_account(value) -> bool:
    if value is None:
        return False
    return str(value).strip().lower() in ACTIVE_STATUS_VALUES


def _is_terminated_employee(value) -> bool:
    if value is None:
        return False
    return str(value).strip().lower() in TERMINATED_STATUS_VALUES


def _is_privileged(value) -> bool:
    if value is None:
        return False
    return str(value).strip().lower() in PRIVILEGED_VALUES


def _days_since(date_value) -> float | None:
    """
    Retourne le nombre de jours écoulés depuis une date, ou None si non
    calculable. Gère aussi le format de date LDAP/Active Directory
    (Generalized Time, ex. '20260807120000.0Z'), non reconnu nativement
    par le parseur de dates générique.
    """
    if pd.isna(date_value) or date_value is None:
        return None

    text_value = str(date_value).strip()
    ldap_match = _LDAP_GENERALIZED_TIME_RE.match(text_value)
    if ldap_match:
        try:
            parsed_dt = datetime.strptime(ldap_match.group(1), "%Y%m%d%H%M%S")
            return (datetime.now() - parsed_dt).days
        except ValueError:
            return None

    try:
        parsed = pd.to_datetime(date_value, errors="coerce")
        if pd.isna(parsed):
            return None
        return (datetime.now() - parsed.to_pydatetime().replace(tzinfo=None)).days
    except Exception:
        return None


def analyze_access(
    df: pd.DataFrame, dormant_threshold_days: int = DORMANT_THRESHOLD_DAYS
) -> pd.DataFrame:
    """
    Analyse un DataFrame standardisé (sortie du module d'ingestion) et
    ajoute les colonnes de diagnostic :

        - days_since_last_login : ancienneté de connexion (None si non calculable)
        - is_dormant : True si inactif depuis plus que le seuil
        - is_terminated_but_active : True si l'employé est parti mais le compte
          reste actif -> anomalie critique
        - is_privileged_flag : True si le compte a des droits privilégiés
        - has_no_manager : True si aucun manager/owner identifié
        - review_action : action recommandée
        - risk_level : niveau de risque (Critique / Élevé / Moyen / Faible)
    """
    df = df.copy()

    if "last_login_date" in df.columns:
        df["days_since_last_login"] = df["last_login_date"].apply(_days_since)
    else:
        logger.warning("Colonne 'last_login_date' absente : détection de dormance désactivée.")
        df["days_since_last_login"] = None

    df["is_dormant"] = df["days_since_last_login"].apply(
        lambda d: d is not None and d > dormant_threshold_days
    )

    if "account_status" in df.columns and "employee_status" in df.columns:
        df["is_terminated_but_active"] = df.apply(
            lambda r: _is_active_account(r["account_status"])
            and _is_terminated_employee(r["employee_status"]),
            axis=1,
        )
    else:
        logger.warning(
            "Colonnes 'account_status' et/ou 'employee_status' absentes : "
            "détection des comptes orphelins post-départ désactivée."
        )
        df["is_terminated_but_active"] = False

    if "is_privileged" in df.columns:
        df["is_privileged_flag"] = df["is_privileged"].apply(_is_privileged)
    else:
        df["is_privileged_flag"] = False

    if "manager" in df.columns:
        df["has_no_manager"] = df["manager"].isna() | (df["manager"].astype(str).str.strip() == "")
    else:
        df["has_no_manager"] = False

    df["review_action"] = df.apply(_determine_action, axis=1)
    df["risk_level"] = df.apply(_determine_risk_level, axis=1)

    logger.info(
        "Analyse terminée. Répartition des actions :\n"
        f"{df['review_action'].value_counts().to_string()}"
    )

    return df


def _determine_action(row) -> str:
    if row["is_terminated_but_active"]:
        return "Révoquer immédiatement"
    if row["is_dormant"] and row["is_privileged_flag"]:
        return "Désactiver (privilégié dormant)"
    if row["is_dormant"]:
        return "Désactiver (dormant)"
    if row["has_no_manager"]:
        return "Identifier un owner"
    return "Aucune action"


def _determine_risk_level(row) -> str:
    if row["is_terminated_but_active"]:
        return "Critique"
    if row["is_dormant"] and row["is_privileged_flag"]:
        return "Critique"
    if row["is_dormant"] or row["has_no_manager"]:
        return "Élevé" if row["is_privileged_flag"] else "Moyen"
    return "Faible"


def summarize(df: pd.DataFrame) -> dict:
    """Retourne un résumé chiffré de l'analyse, utile pour un dashboard/rapport."""
    return {
        "total_accounts": len(df),
        "terminated_but_active": int(df["is_terminated_but_active"].sum()),
        "dormant_accounts": int(df["is_dormant"].sum()),
        "privileged_accounts": int(df["is_privileged_flag"].sum()),
        "privileged_dormant": int((df["is_dormant"] & df["is_privileged_flag"]).sum()),
        "accounts_without_manager": int(df["has_no_manager"].sum()),
        "critical_risk": int((df["risk_level"] == "Critique").sum()),
    }


if __name__ == "__main__":
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent.parent))

    from ingestion.ingest import load_file

    if len(sys.argv) < 2:
        print("Usage : python -m analysis.access_review <chemin_fichier>")
        sys.exit(1)

    df_in = load_file(sys.argv[1])
    df_out = analyze_access(df_in)

    cols = [
        c for c in [
            "username", "full_name", "system", "account_status",
            "employee_status", "days_since_last_login", "is_privileged_flag",
            "review_action", "risk_level",
        ] if c in df_out.columns
    ]
    print(df_out[cols].sort_values("risk_level"))
    print("\nRésumé :", summarize(df_out))
