"""
Acceptations de risque ("risk acceptance") — quand le propriétaire d'un
compte répond et justifie une situation qui déclencherait autrement une
action de revue (compte dormant, privilégié non justifié, conflit SoD,
etc.), le reviewer peut accepter formellement ce risque précis, avec un
commentaire et éventuellement une période de validité.

Principe central (confirmé explicitement avec l'utilisateur, puis
RAFFINÉ après un vrai bug trouvé en testant un compte à PLUSIEURS
constats simultanés) : une acceptation ne couvre QUE le constat PRÉCIS
qui a été accepté — identifié par sa clé technique (ex. 'is_dormant',
'sod_conflict'), pas par le seul review_action affiché. Un compte peut
cumuler plusieurs problèmes indépendants à la fois (ex. dormant ET
conflit SoD) : review_action n'en affiche qu'UN SEUL par priorité,
mais TOUS les indicateurs sous-jacents restent vrais simultanément.
Accepter "le dormant" ne doit donc jamais masquer le conflit SoD, qui
n'a jamais été spécifiquement accepté.

Stockage : une entrée par (compte, système, constat) — un même compte
peut ainsi avoir plusieurs acceptations actives en parallèle, chacune
avec son propre commentaire et sa propre échéance.

Quatre scénarios couverts, chacun testé séparément :
1. Acceptation active (pas expirée, constat toujours vrai) -> exclue
   du tableau de CE contrôle précis, listée dans les Exceptions.
2. Acceptation expirée -> redevient un finding actif pour ce contrôle,
   signalée distinctement ("acceptation expirée, à revalider").
3. Le constat a disparu depuis l'acceptation (l'indicateur est
   redevenu False, ex. le compte s'est reconnecté) -> l'acceptation
   est obsolète ("stale"), ne s'applique plus, rien à revalider.
4. Aucune acceptation -> comportement inchangé.

review_action et risk_score sont recalculés en excluant la
contribution des constats acceptés (via _determine_action et
_compute_risk_score appelés sur une copie où les indicateurs acceptés
sont neutralisés) — sans jamais modifier les indicateurs bruts
eux-mêmes (is_dormant, sod_conflict...), qui restent la vérité de
terrain pour l'affichage des Findings dans le dashboard.
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

# Constats acceptables individuellement, avec leur libellé humain — la
# même liste que celle affichée dans le dashboard (section Findings),
# pour rester cohérent entre ce qui est montré et ce qui est acceptable.
ACCEPTABLE_FINDING_KEYS = {
    "is_terminated_but_active": "Employé parti, compte encore actif",
    "is_dormant": "Compte dormant",
    "is_never_used": "Jamais utilisé depuis sa création",
    "is_password_stale": "Mot de passe périmé",
    "has_non_expiring_password": "Mot de passe n'expirant jamais",
    "has_no_manager": "Aucun manager identifié",
    "is_duplicate_account": "Compte en doublon",
    "is_test_account": "Nom évoquant un compte de test",
    "is_orphaned_account": "Compte générique/orphelin présumé",
    "sod_conflict": "Conflit de séparation des tâches (SoD)",
    "is_locked": "Compte verrouillé",
}


def _entry_key(username, system, control_key) -> str:
    """Clé composite : compte + système + constat précis — permet
    plusieurs acceptations actives en parallèle pour le même compte."""
    return f"{str(username).strip().lower()}::{str(system).strip().lower()}::{control_key}"


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
    username: str, system: str, control_key: str, comment: str,
    accepted_by: str, expiration_date: str | None = None,
    store_path: Path | str = DEFAULT_STORE_PATH,
) -> dict:
    """
    Enregistre une acceptation de risque pour UN constat précis
    (control_key, ex. 'is_dormant') sur un compte donné — n'affecte
    aucun autre constat éventuellement présent simultanément sur ce
    même compte. `expiration_date` au format 'YYYY-MM-DD', ou None pour
    une acceptation sans échéance (toujours visible dans les
    Exceptions, jamais totalement invisible pour autant).
    """
    store_path = Path(store_path)
    key = _entry_key(username, system, control_key)
    with locked(store_path):
        acceptances = load_risk_acceptances(store_path)
        acceptances[key] = {
            "username": str(username),
            "system": str(system),
            "control_key": control_key,
            "control_label": ACCEPTABLE_FINDING_KEYS.get(control_key, control_key),
            "comment": comment,
            "accepted_by": accepted_by,
            "accepted_date": datetime.now().strftime("%Y-%m-%d"),
            "expiration_date": expiration_date,
        }
        store_path.parent.mkdir(parents=True, exist_ok=True)
        with open(store_path, "w", encoding="utf-8") as f:
            json.dump(acceptances, f, ensure_ascii=False, indent=2)
    logger.info(f"Acceptation de risque enregistrée pour '{username}' ({system}) — constat '{control_key}'.")
    return acceptances


def remove_risk_acceptance(
    username: str, system: str, control_key: str, store_path: Path | str = DEFAULT_STORE_PATH,
) -> dict:
    """Retire une acceptation précise (ex. enregistrée par erreur). Ne
    lève pas d'erreur si elle n'existait pas."""
    store_path = Path(store_path)
    key = _entry_key(username, system, control_key)
    with locked(store_path):
        acceptances = load_risk_acceptances(store_path)
        acceptances.pop(key, None)
        store_path.parent.mkdir(parents=True, exist_ok=True)
        with open(store_path, "w", encoding="utf-8") as f:
            json.dump(acceptances, f, ensure_ascii=False, indent=2)
    return acceptances


def is_finding_accepted(row, control_key: str) -> bool:
    """Vrai si CE constat précis (control_key) est actuellement couvert
    par une acceptation valide sur cette ligne — utilisé par le filtrage
    des tableaux de contrôle individuels (report PDF/Word), pour
    n'exclure le compte QUE du contrôle concerné, jamais des autres."""
    accepted = row.get("accepted_finding_keys")
    return bool(accepted) and control_key in accepted


def apply_risk_acceptances(df: pd.DataFrame, store_path: Path | str = DEFAULT_STORE_PATH) -> pd.DataFrame:
    """
    Applique les acceptations enregistrées au DataFrame analysé, par
    constat précis — jamais par compte entier. Ajoute :
    - accepted_finding_keys : liste des clés de constat actuellement
      couvertes par une acceptation valide sur cette ligne (utilisée
      pour le filtrage précis de chaque tableau de contrôle).
    - expired_finding_keys : constats dont l'acceptation a expiré —
      redevenus des findings actifs, mais à signaler distinctement.
    - review_action et risk_level/risk_score recalculés en excluant la
      contribution des constats acceptés (les indicateurs bruts eux-
      mêmes, is_dormant/sod_conflict/etc., restent inchangés).

    N'importe quel indicateur RESTÉ vrai et non couvert par une
    acceptation valide continue de compter normalement — c'est
    exactement ce qui empêche un compte à plusieurs problèmes
    simultanés de voir TOUS ses constats disparaître parce qu'un SEUL
    d'entre eux a été accepté.
    """
    from analysis.access_review import _determine_action, _compute_risk_score, _determine_risk_level

    acceptances = load_risk_acceptances(store_path)
    df["accepted_finding_keys"] = [[] for _ in range(len(df))]
    df["expired_finding_keys"] = [[] for _ in range(len(df))]
    df["accepted_findings_detail"] = [[] for _ in range(len(df))]

    if not acceptances or "username" not in df.columns or "system" not in df.columns:
        return df

    today = date.today()

    for idx, row in df.iterrows():
        accepted_keys, expired_keys = [], []
        for control_key in ACCEPTABLE_FINDING_KEYS:
            if not row.get(control_key, False):
                continue  # constat pas (ou plus) vrai -> rien à accepter/expirer ici
            entry = acceptances.get(_entry_key(row["username"], row.get("system", ""), control_key))
            if entry is None:
                continue
            expiration_str = entry.get("expiration_date")
            is_expired = False
            if expiration_str:
                try:
                    is_expired = date.fromisoformat(expiration_str) < today
                except ValueError:
                    is_expired = False
            if is_expired:
                expired_keys.append(control_key)
            else:
                accepted_keys.append(control_key)

        if accepted_keys or expired_keys:
            df.at[idx, "accepted_finding_keys"] = accepted_keys
            df.at[idx, "expired_finding_keys"] = expired_keys
            detail = []
            for key in accepted_keys:
                entry = acceptances.get(_entry_key(row["username"], row.get("system", ""), key))
                if entry:
                    detail.append({
                        "control_label": entry.get("control_label", key),
                        "comment": entry.get("comment", ""),
                        "accepted_by": entry.get("accepted_by", ""),
                        "expiration_date": entry.get("expiration_date") or "",
                    })
            df.at[idx, "accepted_findings_detail"] = detail

        if accepted_keys:
            # Recalcule review_action et risk_score en excluant
            # UNIQUEMENT les constats acceptés — sur une COPIE de la
            # ligne. Les indicateurs bruts (is_dormant, sod_conflict...)
            # restent inchangés dans le DataFrame, seule la vue
            # "findings restants après acceptation" sert à ce recalcul.
            neutralized = row.copy()
            for key in accepted_keys:
                neutralized[key] = False
            df.at[idx, "review_action"] = _determine_action(neutralized)
            score, reasons = _compute_risk_score(neutralized)
            df.at[idx, "risk_score"] = score
            df.at[idx, "risk_score_reasons"] = reasons
            df.at[idx, "risk_level"] = _determine_risk_level(neutralized)

    return df


def get_accepted_findings_detail(df: pd.DataFrame, store_path: Path | str = DEFAULT_STORE_PATH) -> list[dict]:
    """
    Détail complet (une entrée par couple compte+constat accepté) pour
    la section Exceptions des rapports — un même compte peut apparaître
    plusieurs fois s'il a plusieurs constats acceptés séparément.
    Lu directement depuis la colonne 'accepted_findings_detail' déjà
    calculée par apply_risk_acceptances (pas de nouvelle lecture du
    fichier de stockage nécessaire ici) — `store_path` n'est conservé
    que pour compatibilité d'appel, non utilisé.
    """
    if "accepted_findings_detail" not in df.columns:
        return []
    rows = []
    for _, row in df.iterrows():
        for detail in row.get("accepted_findings_detail") or []:
            rows.append({
                "username": row["username"],
                "system": row.get("system", ""),
                "accepted_finding": detail.get("control_label", ""),
                "comment": detail.get("comment", ""),
                "accepted_by": detail.get("accepted_by", ""),
                "expiration_date": detail.get("expiration_date") or "",
            })
    return rows
