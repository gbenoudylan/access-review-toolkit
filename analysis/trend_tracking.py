"""
Module de suivi de tendance dans le temps.

Historise les indicateurs clés d'un cycle de revue (comptes dormants,
mots de passe périmés, doublons, etc.) pour répondre à une question que
le rapport ponctuel ne peut pas traiter seul : "est-ce que ça s'améliore
ou empire d'un cycle à l'autre ?", pas seulement "combien aujourd'hui ?".

Enregistrement explicite (pas automatique à chaque analyse) : un
reviewer charge souvent un fichier plusieurs fois avant d'arriver à la
version définitive (test de seuils, correction d'un mauvais upload) —
tout enregistrer polluerait l'historique de faux points.

Le périmètre (quels systèmes sont couverts) est enregistré avec chaque
instantané, pas seulement les chiffres : deux cycles ne couvrant pas les
mêmes systèmes ne sont pas directement comparables en valeur brute (10
comptes dormants sur un périmètre AD seul n'a rien à voir avec 10 sur un
périmètre AD+SAP) — la comparaison doit rester possible par système pris
séparément, pas seulement en tendance globale trompeuse.
"""

from __future__ import annotations
import json
import logging
from datetime import datetime
from pathlib import Path

import pandas as pd

logger = logging.getLogger("trend_tracking")

DEFAULT_TREND_STORE_PATH = Path(__file__).parent.parent / "data" / "trend_history.json"

# Indicateurs bruts enregistrés à chaque instantané, en plus du total de
# comptes et de la répartition par niveau de risque — un sous-ensemble
# des colonnes booléennes déjà calculées par analyze_access(), celles qui
# ont le plus de sens à suivre dans la durée pour un comité de pilotage.
TRACKED_BOOLEAN_METRICS = [
    "is_dormant", "is_never_used", "is_password_stale", "is_duplicate_account",
    "is_locked", "is_terminated_but_active", "sod_conflict",
]


def _load_store(store_path: Path | str) -> list[dict]:
    store_path = Path(store_path)
    if not store_path.exists():
        return []
    try:
        with open(store_path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"Historique de tendance corrompu ou illisible ({e}) : redémarrage à vide.")
        return []


def record_cycle_snapshot(
    df: pd.DataFrame, period_label: str, store_path: Path | str = DEFAULT_TREND_STORE_PATH,
    recorded_by: str = "",
) -> dict:
    """
    Enregistre un instantané des indicateurs clés du cycle courant dans
    l'historique de tendance — action EXPLICITE (appelée uniquement sur
    demande du reviewer, jamais automatiquement à chaque analyse).

    Le périmètre (systèmes distincts présents dans `df`) est enregistré
    avec l'instantané, ET les indicateurs sont calculés À LA FOIS
    globalement (tous systèmes confondus) ET système par système : sans
    cette seconde décomposition, filtrer plus tard sur "AD seul" ne
    ferait que garder les CYCLES qui incluent AD, tout en affichant les
    totaux globaux du cycle (SAP compris) — pas vraiment comparable d'un
    cycle à l'autre si le reste du périmètre change. Avec la
    décomposition par système, une comparaison "AD seul" reste valable
    même si SAP a été ajouté ou retiré entre deux cycles.

    Retourne l'instantané enregistré (dict), pour affichage immédiat sans
    recharger tout l'historique.
    """
    store_path = Path(store_path)
    history = _load_store(store_path)

    systems = sorted(df["system"].dropna().astype(str).str.strip().unique()) if "system" in df.columns else []
    risk_counts = df["risk_level"].value_counts().to_dict() if "risk_level" in df.columns else {}

    def _metrics_for(subset: pd.DataFrame) -> dict:
        return {
            metric: int(subset[metric].sum())
            for metric in TRACKED_BOOLEAN_METRICS if metric in subset.columns
        }

    by_system = {}
    if "system" in df.columns:
        for sys_name in systems:
            subset = df[df["system"].astype(str).str.strip() == sys_name]
            by_system[sys_name] = {"total_accounts": len(subset), "metrics": _metrics_for(subset)}

    snapshot = {
        "date": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "period_label": period_label or "",
        "recorded_by": recorded_by or "",
        "systems": systems,
        "total_accounts": len(df),
        "risk_counts": {
            "Critique": int(risk_counts.get("Critique", 0)),
            "Élevé": int(risk_counts.get("Élevé", 0)),
            "Moyen": int(risk_counts.get("Moyen", 0)),
            "Faible": int(risk_counts.get("Faible", 0)),
        },
        "metrics": _metrics_for(df),
        "by_system": by_system,
    }

    history.append(snapshot)
    store_path.parent.mkdir(parents=True, exist_ok=True)
    with open(store_path, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)

    logger.info(
        f"Instantané de tendance enregistré ({snapshot['date']}, périmètre : "
        f"{', '.join(systems) or 'non renseigné'})."
    )
    return snapshot


def load_trend_history(
    store_path: Path | str = DEFAULT_TREND_STORE_PATH, system: str | None = None,
) -> pd.DataFrame:
    """
    Charge l'historique de tendance sous forme de tableau, une ligne par
    cycle enregistré, triée par date — prête à être tracée directement.

    `system` : si fourni, retourne les indicateurs calculés SPÉCIFIQUEMENT
    pour ce système (pas les totaux globaux du cycle) pour chaque cycle
    où il était présent dans le périmètre — une comparaison réellement
    apples-to-apples même si d'autres systèmes ont été ajoutés ou retirés
    entre deux cycles. Sans `system`, retourne les totaux globaux de
    chaque cycle (tous systèmes confondus), avec le périmètre affiché en
    clair pour que tout changement de périmètre reste visible plutôt que
    silencieusement mélangé à une vraie évolution.
    """
    history = _load_store(store_path)
    if not history:
        return pd.DataFrame(
            columns=["date", "period_label", "systems", "total_accounts"] + TRACKED_BOOLEAN_METRICS
        )

    rows = []
    for snap in history:
        if system is not None:
            if system not in snap.get("by_system", {}):
                continue
            sys_data = snap["by_system"][system]
            row = {
                "date": snap["date"], "period_label": snap.get("period_label", ""),
                "systems": system, "total_accounts": sys_data.get("total_accounts", 0),
            }
            row.update(sys_data.get("metrics", {}))
        else:
            row = {
                "date": snap["date"],
                "period_label": snap.get("period_label", ""),
                "systems": ", ".join(snap.get("systems", [])) or "Non renseigné",
                "total_accounts": snap.get("total_accounts", 0),
            }
            row.update(snap.get("risk_counts", {}))
            row.update(snap.get("metrics", {}))
        rows.append(row)

    df = pd.DataFrame(rows)
    if not df.empty:
        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values("date").reset_index(drop=True)
    return df


def scope_changed_between(previous_snapshot: dict, current_snapshot: dict) -> bool:
    """True si le périmètre système diffère entre deux instantanés
    consécutifs — pour signaler visuellement un changement de périmètre
    plutôt que de laisser croire à une vraie évolution des indicateurs."""
    return set(previous_snapshot.get("systems", [])) != set(current_snapshot.get("systems", []))
