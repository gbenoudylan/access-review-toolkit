"""
Mémorisation des correspondances apprises manuellement pour les
VALEURS de statut (pas les noms de colonnes) — ex. 'Valid' → 'active',
'Pending' → 'inactive'. Même principe que custom_column_mappings.py,
mais pour des valeurs, pas des noms de champs.

Magasin séparé (custom_status_mappings.json) pour la même raison que
les autres : les valeurs de statut sont propres à chaque système source,
sans lien avec les noms de colonnes.

Deux cibles possibles : 'active' ou 'inactive'.
"""
from __future__ import annotations
import json
import re
import unicodedata
from pathlib import Path
from analysis.file_lock import locked

DEFAULT_STORE_PATH = Path(__file__).parent.parent / "data" / "custom_status_mappings.json"


def _normalize(value: str) -> str:
    """Même normalisation que pour les colonnes — insensible à la casse,
    aux accents et aux espaces multiples."""
    s = unicodedata.normalize("NFKD", str(value)).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def load_custom_status_mappings(
    store_path: Path | str = DEFAULT_STORE_PATH,
) -> dict[str, str]:
    """Retourne {valeur_normalisée: 'active'|'inactive'}."""
    store_path = Path(store_path)
    if not store_path.exists():
        return {}
    try:
        with open(store_path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def save_custom_status_mapping(
    raw_value: str, target: str,
    store_path: Path | str = DEFAULT_STORE_PATH,
) -> dict:
    """Enregistre une correspondance valeur → 'active'|'inactive'."""
    assert target in ("active", "inactive"), f"Cible invalide : {target!r}"
    store_path = Path(store_path)
    key = _normalize(raw_value)
    with locked(store_path):
        mappings = load_custom_status_mappings(store_path)
        mappings[key] = target
        store_path.parent.mkdir(parents=True, exist_ok=True)
        with open(store_path, "w", encoding="utf-8") as f:
            json.dump(mappings, f, ensure_ascii=False, indent=2)
    return mappings


def forget_custom_status_mapping(
    raw_value: str,
    store_path: Path | str = DEFAULT_STORE_PATH,
) -> dict:
    store_path = Path(store_path)
    key = _normalize(raw_value)
    with locked(store_path):
        mappings = load_custom_status_mappings(store_path)
        mappings.pop(key, None)
        store_path.parent.mkdir(parents=True, exist_ok=True)
        with open(store_path, "w", encoding="utf-8") as f:
            json.dump(mappings, f, ensure_ascii=False, indent=2)
    return mappings


def resolve_status_value(
    raw_value: str,
    custom_mappings: dict[str, str] | None = None,
) -> str | None:
    """
    Retourne 'active', 'inactive', ou None si la valeur est inconnue.
    None = pire cas pour l'audit (traité comme potentiellement actif).
    """
    if raw_value is None:
        return None
    if custom_mappings:
        key = _normalize(raw_value)
        if key in custom_mappings:
            return custom_mappings[key]
    return None
