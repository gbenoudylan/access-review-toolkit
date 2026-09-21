"""
Qualification manuelle des droits/permissions : l'auditeur peut marquer
chaque droit distinct trouvé dans la colonne user_rights comme :
  - "admin"    : compte à risque élevé, à revoir en priorité
  - "standard" : droit fonctionnel normal, sans risque particulier

Stocké dans data/custom_rights_mappings.json.
Complémentaire à la détection automatique par mots-clés (PRIVILEGED_ROLE_KEYWORDS) :
un droit non reconnu automatiquement peut être qualifié ici sans toucher au code.
"""
from __future__ import annotations
import json
import re
import unicodedata
from pathlib import Path
from analysis.file_lock import locked

DEFAULT_STORE_PATH = Path(__file__).parent.parent / "data" / "custom_rights_mappings.json"


def _normalize(value: str) -> str:
    s = unicodedata.normalize("NFKD", str(value)).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def load_custom_rights_mappings(
    store_path: Path | str = DEFAULT_STORE_PATH,
) -> dict[str, str]:
    """Retourne {droit_normalisé: 'admin'|'standard'}."""
    store_path = Path(store_path)
    if not store_path.exists():
        return {}
    try:
        with open(store_path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def save_custom_rights_mapping(
    raw_right: str, category: str,
    store_path: Path | str = DEFAULT_STORE_PATH,
) -> dict:
    """Enregistre un droit comme 'admin' ou 'standard'."""
    assert category in ("admin", "standard"), f"Catégorie invalide : {category!r}"
    store_path = Path(store_path)
    key = raw_right.strip()  # clé = valeur brute exacte (préserver la casse pour l'affichage)
    with locked(store_path):
        mappings = load_custom_rights_mappings(store_path)
        mappings[key] = category
        store_path.parent.mkdir(parents=True, exist_ok=True)
        with open(store_path, "w", encoding="utf-8") as f:
            json.dump(mappings, f, ensure_ascii=False, indent=2)
    return mappings


def forget_custom_rights_mapping(
    raw_right: str,
    store_path: Path | str = DEFAULT_STORE_PATH,
) -> dict:
    store_path = Path(store_path)
    key = raw_right.strip()
    with locked(store_path):
        mappings = load_custom_rights_mappings(store_path)
        mappings.pop(key, None)
        store_path.parent.mkdir(parents=True, exist_ok=True)
        with open(store_path, "w", encoding="utf-8") as f:
            json.dump(mappings, f, ensure_ascii=False, indent=2)
    return mappings


def get_all_distinct_rights_from_df(df) -> list[str]:
    """
    Extrait tous les droits distincts de la colonne user_rights (et role)
    du DataFrame — chaque cellule peut contenir plusieurs droits séparés par
    des retours à la ligne ou des points-virgules.
    """
    rights_set = set()
    for col in ("user_rights", "role"):
        if col not in df.columns:
            continue
        for raw in df[col].astype(str).fillna("").unique():
            if not raw or raw in ("nan", "none", ""):
                continue
            for r in (raw.split("\n") if "\n" in raw
                      else raw.split(";") if ";" in raw
                      else raw.split(",")):
                r = r.strip()
                if r and len(r) > 1:
                    rights_set.add(r)
    return sorted(rights_set, key=str.lower)
