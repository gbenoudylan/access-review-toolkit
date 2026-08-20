"""
Module de workflow de validation.

Chaque compte flagué par l'analyse (dormant, orphelin, conflit SoD...) doit
être explicitement validé par un responsable — c'est le principe même
d'une revue d'accès : quelqu'un doit se prononcer, pas juste constater.

Ce module ajoute un statut de traitement à chaque anomalie ("En attente",
"Validé — accès légitime", "Révoqué"), avec horodatage et nom du
validateur, et persiste ces décisions dans un fichier local entre deux
exécutions — pour que le suivi survive d'une revue mensuelle à l'autre.

Stockage volontairement simple (JSON local) : suffisant pour un usage
individuel ou en petite équipe. Pour un usage à grande échelle partagé
entre plusieurs personnes, une vraie base de données serait préférable.
"""

from __future__ import annotations
import json
import logging
from datetime import datetime
from pathlib import Path

import pandas as pd

logger = logging.getLogger("workflow")

DEFAULT_STORE_PATH = Path(__file__).parent.parent / "data" / "review_decisions.json"

VALID_STATUSES = ["En attente", "Validé - accès légitime", "Révoqué"]


def _account_key(username: str, system: str) -> str:
    """Clé unique par compte : un même username peut avoir plusieurs comptes système."""
    return f"{username}::{system}"


def _load_store(store_path: Path | str) -> dict:
    store_path = Path(store_path)
    if store_path.exists():
        try:
            with open(store_path, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            logger.warning("Fichier de décisions corrompu, redémarrage à vide.")
    return {}


def _save_store(store_path: Path | str, store: dict) -> None:
    store_path = Path(store_path)
    store_path.parent.mkdir(parents=True, exist_ok=True)
    with open(store_path, "w", encoding="utf-8") as f:
        json.dump(store, f, indent=2, ensure_ascii=False)


def apply_review_decision(
    username: str,
    system: str,
    status: str,
    validated_by: str = "",
    comment: str = "",
    store_path: Path = DEFAULT_STORE_PATH,
) -> None:
    """Enregistre une décision de revue pour un compte donné."""
    if status not in VALID_STATUSES:
        raise ValueError(f"Statut invalide : {status}. Attendu : {VALID_STATUSES}")

    store = _load_store(store_path)
    key = _account_key(username, system)
    store[key] = {
        "status": status,
        "validated_by": validated_by,
        "comment": comment,
        "date": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }
    _save_store(store_path, store)
    logger.info(f"Décision enregistrée pour {key} : {status}")


def attach_review_status(
    df: pd.DataFrame, store_path: Path = DEFAULT_STORE_PATH
) -> pd.DataFrame:
    """
    Ajoute le statut de revue à chaque ligne du DataFrame, à partir des
    décisions déjà enregistrées. Les comptes sans décision antérieure sont
    marqués 'En attente' par défaut.
    """
    df = df.copy()
    store = _load_store(store_path)

    if "username" not in df.columns or "system" not in df.columns:
        logger.warning("Colonnes 'username'/'system' absentes : workflow de validation désactivé.")
        df["review_status"] = "En attente"
        df["validated_by"] = ""
        df["review_comment"] = ""
        df["review_date"] = ""
        return df

    def _lookup(row, field):
        key = _account_key(row["username"], row["system"])
        return store.get(key, {}).get(field, "" if field != "status" else "En attente")

    df["review_status"] = df.apply(lambda r: _lookup(r, "status"), axis=1)
    df["validated_by"] = df.apply(lambda r: _lookup(r, "validated_by"), axis=1)
    df["review_comment"] = df.apply(lambda r: _lookup(r, "comment"), axis=1)
    df["review_date"] = df.apply(lambda r: _lookup(r, "date"), axis=1)

    return df


def review_summary(df: pd.DataFrame) -> dict:
    """Résumé chiffré de l'avancement des validations."""
    if "review_status" not in df.columns:
        return {}
    counts = df["review_status"].value_counts().to_dict()
    return {
        "en_attente": counts.get("En attente", 0),
        "valide": counts.get("Validé - accès légitime", 0),
        "revoque": counts.get("Révoqué", 0),
        "taux_traitement": round(
            100 * (len(df) - counts.get("En attente", 0)) / len(df), 1
        ) if len(df) else 0,
    }


if __name__ == "__main__":
    import sys
    from pathlib import Path as _Path
    sys.path.insert(0, str(_Path(__file__).parent.parent))

    logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")

    if len(sys.argv) < 2:
        print(
            "Usage :\n"
            "  python -m analysis.review_workflow <fichier>  # affiche le statut actuel\n"
            "  python -m analysis.review_workflow decide <username> <system> <status> [validateur]"
        )
        sys.exit(1)

    if sys.argv[1] == "decide":
        _, _, username, system, status = sys.argv[:5]
        validated_by = sys.argv[5] if len(sys.argv) > 5 else ""
        apply_review_decision(username, system, status, validated_by)
    else:
        from ingestion.ingest import load_file
        from analysis.access_review import analyze_access

        df_in = load_file(sys.argv[1])
        df_out = attach_review_status(analyze_access(df_in))
        print(df_out[["username", "system", "risk_level", "review_status"]])
        print("\nRésumé :", review_summary(df_out))
