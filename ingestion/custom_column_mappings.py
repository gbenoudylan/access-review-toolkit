"""
Correspondances de colonnes apprises manuellement.

Aucune liste de variantes de noms de colonnes, aussi longue soit-elle,
ne peut couvrir à l'avance tous les noms qu'un futur export utilisera —
les systèmes changent, les conventions de nommage varient d'une
entreprise à l'autre, et personne ne sera toujours là pour mettre à jour
le code. Ce module permet à un utilisateur, via le dashboard, de
corriger manuellement une colonne non reconnue une seule fois — la
correction est alors mémorisée ici et appliquée automatiquement à tout
futur fichier portant exactement le même nom de colonne, sans jamais
toucher au code.
"""

from __future__ import annotations
import json
import logging
from pathlib import Path

from analysis.file_lock import locked

logger = logging.getLogger("custom_column_mappings")

DEFAULT_STORE_PATH = Path(__file__).parent.parent / "data" / "custom_column_mappings.json"


def _normalize_key(raw_column_name: str) -> str:
    """Même normalisation que ingestion.ingest._normalize (espaces/casse/
    séparateurs) — la clé de correspondance doit matcher exactement ce que
    _match_column compare, sans quoi une correspondance apprise pourrait
    silencieusement ne jamais s'appliquer."""
    return str(raw_column_name).strip().lower().replace("_", " ").replace("-", " ").replace("/", " ")


def load_custom_column_mappings(store_path: Path | str = DEFAULT_STORE_PATH) -> dict:
    """Charge les correspondances apprises (colonne normalisée -> champ
    standard). Fichier absent ou corrompu -> dict vide, jamais d'erreur :
    l'apprentissage manuel est un confort, pas une dépendance critique."""
    store_path = Path(store_path)
    if not store_path.exists():
        return {}
    try:
        with open(store_path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"Fichier de correspondances apprises corrompu ou illisible ({e}) : ignoré.")
        return {}


def save_custom_column_mapping(
    raw_column_name: str, standard_field: str, store_path: Path | str = DEFAULT_STORE_PATH,
) -> dict:
    """
    Enregistre une correspondance confirmée par l'utilisateur, pour
    reconnaissance automatique de toute colonne future portant EXACTEMENT
    le même nom (normalisé). Verrou de fichier : plusieurs personnes
    peuvent utiliser le dashboard en même temps.

    Retourne l'ensemble complet des correspondances après l'ajout.
    """
    store_path = Path(store_path)
    key = _normalize_key(raw_column_name)
    with locked(store_path):
        mappings = load_custom_column_mappings(store_path)
        mappings[key] = standard_field
        store_path.parent.mkdir(parents=True, exist_ok=True)
        with open(store_path, "w", encoding="utf-8") as f:
            json.dump(mappings, f, ensure_ascii=False, indent=2)
    logger.info(f"Correspondance apprise : '{raw_column_name}' -> '{standard_field}'.")
    return mappings


def forget_custom_column_mapping(
    raw_column_name: str, store_path: Path | str = DEFAULT_STORE_PATH,
) -> dict:
    """Retire une correspondance apprise (ex. correction faite par
    erreur). Ne lève pas d'erreur si elle n'existait pas."""
    store_path = Path(store_path)
    key = _normalize_key(raw_column_name)
    with locked(store_path):
        mappings = load_custom_column_mappings(store_path)
        mappings.pop(key, None)
        store_path.parent.mkdir(parents=True, exist_ok=True)
        with open(store_path, "w", encoding="utf-8") as f:
            json.dump(mappings, f, ensure_ascii=False, indent=2)
    return mappings
