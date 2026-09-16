"""
Acceptations de risque ("risk acceptance") — quand le propriétaire d'un
compte répond et justifie une situation qui déclencherait autrement une
action de revue (compte dormant, privilégié non justifié, etc.), le
reviewer peut accepter formellement ce risque précis, avec un
commentaire et éventuellement une période de validité.

Principe central (confirmé explicitement avec l'utilisateur) : une
acceptation ne couvre QUE le constat précis qui a été accepté, jamais
le compte dans l'absolu — si un problème DIFFÉRENT apparaît plus tard
sur ce même compte (ou si le même type de problème réapparaît après
une période où il avait disparu), l'ancienne acceptation ne s'applique
pas automatiquement. Cela évite qu'une acceptation ancienne masque
silencieusement un risque réellement nouveau.

Quatre scénarios couverts, chacun testé séparément :
1. Acceptation active (pas expirée, constat inchangé) -> exclue des
   findings, listée dans les Exceptions.
2. Acceptation expirée -> redevient un finding normal, mais signalée
   distinctement ("acceptation expirée, à revalider") plutôt que de
   redisparaître silencieusement dans la masse des findings.
3. Le constat a changé depuis l'acceptation (autre review_action
   qu'au moment de l'acceptation) -> l'ancienne acceptation ne
   s'applique pas, c'est un nouveau finding à part entière.
4. Aucune acceptation -> comportement inchangé.
"""

from __future__ import annotations
import json
import logging
from datetime import datetime, date
from pathlib import Path

import pandas as pd

from analysis.file_lock import locked

logger = logging.getLogger("risk_acceptance")

DEFAULT_STORE_PATH = Path(__file__).parent.parent / "data" / "risk_acceptances.json"


def _account_key(username, system) -> str:
    """Même normalisation que review_workflow._account_key (casse/espaces)
    — une acceptation enregistrée pour 'JDupont' doit s'appliquer à
    'jdupont' rencontré dans un fichier ultérieur."""
    return f"{str(username).strip().lower()}::{str(system).strip().lower()}"


def load_risk_acceptances(store_path: Path | str = DEFAULT_STORE_PATH) -> dict:
    """Charge les acceptations enregistrées. Fichier absent ou corrompu
    -> dict vide, jamais d'erreur (best-effort, comme les autres
    magasins JSON partagés de l'outil)."""
    store_path = Path(store_path)
    if not store_path.exists():
        return {}
    try:
        with open(store_path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"Fichier d'acceptations de risque corrompu ou illisible ({e}) : ignoré.")
        return {}


def save_risk_acceptance(
    username: str, system: str, accepted_review_action: str, comment: str,
    accepted_by: str, expiration_date: str | None = None,
    store_path: Path | str = DEFAULT_STORE_PATH,
) -> dict:
    """
    Enregistre une acceptation de risque pour un compte, liée au constat
    PRÉCIS (accepted_review_action) qui a motivé la décision — pas au
    compte dans l'absolu. `expiration_date` au format 'YYYY-MM-DD',
    ou None pour une acceptation sans échéance (toujours visible dans
    les Exceptions, jamais totalement invisible pour autant).
    """
    store_path = Path(store_path)
    key = _account_key(username, system)
    with locked(store_path):
        acceptances = load_risk_acceptances(store_path)
        acceptances[key] = {
            "username": str(username),
            "system": str(system),
            "accepted_review_action": accepted_review_action,
            "comment": comment,
            "accepted_by": accepted_by,
            "accepted_date": datetime.now().strftime("%Y-%m-%d"),
            "expiration_date": expiration_date,
        }
        store_path.parent.mkdir(parents=True, exist_ok=True)
        with open(store_path, "w", encoding="utf-8") as f:
            json.dump(acceptances, f, ensure_ascii=False, indent=2)
    logger.info(f"Acceptation de risque enregistrée pour '{username}' ({system}).")
    return acceptances


def remove_risk_acceptance(username: str, system: str, store_path: Path | str = DEFAULT_STORE_PATH) -> dict:
    """Retire une acceptation (ex. enregistrée par erreur). Ne lève pas
    d'erreur si elle n'existait pas."""
    store_path = Path(store_path)
    key = _account_key(username, system)
    with locked(store_path):
        acceptances = load_risk_acceptances(store_path)
        acceptances.pop(key, None)
        store_path.parent.mkdir(parents=True, exist_ok=True)
        with open(store_path, "w", encoding="utf-8") as f:
            json.dump(acceptances, f, ensure_ascii=False, indent=2)
    return acceptances


def apply_risk_acceptances(df: pd.DataFrame, store_path: Path | str = DEFAULT_STORE_PATH) -> pd.DataFrame:
    """
    Applique les acceptations enregistrées au DataFrame analysé. Ajoute :
    - is_risk_accepted : True si une acceptation VALIDE (non expirée,
      constat inchangé depuis l'acceptation) s'applique à cette ligne.
    - risk_acceptance_comment, risk_acceptance_expiration,
      risk_acceptance_accepted_by : détail de l'acceptation appliquée.
    - is_risk_acceptance_expired : True si une acceptation existe pour
      ce compte mais que sa date d'expiration est dépassée — le finding
      redevient actif, mais cette colonne permet de le signaler
      distinctement ("à revalider") plutôt que d'être un finding anonyme.
    - is_risk_acceptance_stale : True si une acceptation existe pour ce
      compte mais que le constat actuel diffère de celui accepté — la
      colonne 'review_action' garde alors sa valeur actuelle normale
      (nouveau problème, pas couvert par l'ancienne acceptation).

    Quand is_risk_accepted est True, review_action et risk_level sont
    remplacés par une valeur dédiée ('Exception (risque accepté)' /
    'Accepté') afin que ces comptes n'apparaissent plus comme des
    findings actifs dans les comptages et graphiques du rapport.
    """
    acceptances = load_risk_acceptances(store_path)

    df["is_risk_accepted"] = False
    df["risk_acceptance_comment"] = ""
    df["risk_acceptance_expiration"] = ""
    df["risk_acceptance_accepted_by"] = ""
    df["risk_acceptance_accepted_finding"] = ""
    df["is_risk_acceptance_expired"] = False
    df["is_risk_acceptance_stale"] = False

    if not acceptances or "username" not in df.columns or "system" not in df.columns:
        return df

    today = date.today()

    for idx, row in df.iterrows():
        key = _account_key(row["username"], row.get("system", ""))
        record = acceptances.get(key)
        if record is None:
            continue

        current_action = row.get("review_action")
        accepted_action = record.get("accepted_review_action")
        expiration_str = record.get("expiration_date")
        is_expired = False
        if expiration_str:
            try:
                is_expired = date.fromisoformat(expiration_str) < today
            except ValueError:
                is_expired = False  # date mal formée : traitée comme sans échéance plutôt que de planter

        if current_action != accepted_action:
            # Le constat a changé depuis l'acceptation : elle ne
            # s'applique plus, c'est un nouveau problème à part entière.
            df.at[idx, "is_risk_acceptance_stale"] = True
            continue

        if is_expired:
            # Acceptation expirée : redevient un finding actif, mais
            # signalé distinctement pour attirer l'attention plutôt que
            # de redisparaître silencieusement dans la masse.
            df.at[idx, "is_risk_acceptance_expired"] = True
            continue

        df.at[idx, "is_risk_accepted"] = True
        df.at[idx, "risk_acceptance_comment"] = record.get("comment", "")
        df.at[idx, "risk_acceptance_expiration"] = expiration_str or ""
        df.at[idx, "risk_acceptance_accepted_by"] = record.get("accepted_by", "")
        df.at[idx, "risk_acceptance_accepted_finding"] = accepted_action
        df.at[idx, "review_action"] = "Exception (risque accepté)"
        df.at[idx, "risk_level"] = "Accepté"

    return df
