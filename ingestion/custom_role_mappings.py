"""
Mémorisation des correspondances apprises manuellement pour les NOMS DE
RÔLES dans la détection SoD — ex. 'AP Resp' → 'MTN_AP - Responsable',
'SysAdmin' → 'System Administrator'. Complément du fuzzy matching : là
où le fuzzy rate (abréviations trop courtes, noms propres MTN très
spécifiques), le mapping manuel prend le relais.

Magasin séparé (custom_role_mappings.json), propre à ce domaine.
"""
from __future__ import annotations
import json
import re
import unicodedata
from pathlib import Path
from analysis.file_lock import locked

DEFAULT_STORE_PATH = Path(__file__).parent.parent / "data" / "custom_role_mappings.json"


def _normalize(value: str) -> str:
    s = unicodedata.normalize("NFKD", str(value)).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def load_custom_role_mappings(
    store_path: Path | str = DEFAULT_STORE_PATH,
) -> dict[str, str]:
    """Retourne {nom_source: nom_standard_dans_la_matrice_SoD}."""
    store_path = Path(store_path)
    if not store_path.exists():
        return {}
    try:
        with open(store_path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def save_custom_role_mapping(
    raw_role: str, standard_role: str,
    store_path: Path | str = DEFAULT_STORE_PATH,
) -> dict:
    store_path = Path(store_path)
    with locked(store_path):
        mappings = load_custom_role_mappings(store_path)
        mappings[raw_role.strip()] = standard_role.strip()
        store_path.parent.mkdir(parents=True, exist_ok=True)
        with open(store_path, "w", encoding="utf-8") as f:
            json.dump(mappings, f, ensure_ascii=False, indent=2)
    return mappings


def forget_custom_role_mapping(
    raw_role: str,
    store_path: Path | str = DEFAULT_STORE_PATH,
) -> dict:
    store_path = Path(store_path)
    with locked(store_path):
        mappings = load_custom_role_mappings(store_path)
        mappings.pop(raw_role.strip(), None)
        store_path.parent.mkdir(parents=True, exist_ok=True)
        with open(store_path, "w", encoding="utf-8") as f:
            json.dump(mappings, f, ensure_ascii=False, indent=2)
    return mappings
