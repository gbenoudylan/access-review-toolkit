"""
Module d'ingestion universelle pour les exports d'accès/comptes.

Logique identique à celle validée sur le projet de gestion des
vulnérabilités : détection automatique de la ligne d'en-tête, gestion des
CSV mal formés, mapping des colonnes vers des noms standards internes.
Seul le référentiel de mapping (config/column_mapping.py) change de domaine.
"""

from __future__ import annotations
import logging
from pathlib import Path

import pandas as pd

try:
    from rapidfuzz import fuzz
    _HAS_RAPIDFUZZ = True
except ImportError:
    _HAS_RAPIDFUZZ = False

from config.column_mapping import COLUMN_MAPPING, REQUIRED_FIELDS

logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")
logger = logging.getLogger("ingestion")


class IngestionError(Exception):
    """Erreur levée quand un fichier ne peut pas être exploité de façon fiable."""


def _normalize(text: str) -> str:
    return str(text).strip().lower().replace("_", " ").replace("-", " ")


def _score_header_row(row: pd.Series) -> int:
    all_variants = {
        _normalize(v) for variants in COLUMN_MAPPING.values() for v in variants
    }
    score = 0
    for cell in row:
        if pd.isna(cell):
            continue
        if _normalize(cell) in all_variants:
            score += 1
    return score


def _detect_header_row(raw: pd.DataFrame, max_scan_rows: int = 15) -> int:
    best_row, best_score = 0, -1
    for i in range(min(max_scan_rows, len(raw))):
        score = _score_header_row(raw.iloc[i])
        if score > best_score:
            best_row, best_score = i, score
    if best_score <= 0:
        logger.warning("Aucune ligne d'en-tête reconnue, utilisation de la ligne 0.")
        return 0
    logger.info(f"En-tête détecté à la ligne {best_row} (score={best_score}).")
    return best_row


def _match_column(col_name: str, threshold: int = 85) -> str | None:
    col_norm = _normalize(col_name)

    for standard_name, variants in COLUMN_MAPPING.items():
        if col_norm in [_normalize(v) for v in variants]:
            return standard_name

    if _HAS_RAPIDFUZZ:
        best_field, best_score = None, 0
        for standard_name, variants in COLUMN_MAPPING.items():
            for v in variants:
                # token_sort_ratio : insensible à l'ordre des mots
                # (ex. "Statut compte" vs "Compte statut")
                s = fuzz.token_sort_ratio(col_norm, _normalize(v))
                if s > best_score:
                    best_field, best_score = standard_name, s
        if best_score >= threshold:
            logger.info(f"Colonne '{col_name}' -> '{best_field}' (fuzzy, score={best_score}).")
            return best_field

    return None


def standardize_columns(df: pd.DataFrame) -> pd.DataFrame:
    rename_map, unmatched = {}, []
    for col in df.columns:
        matched = _match_column(col)
        if matched:
            rename_map[col] = matched
        else:
            unmatched.append(col)
    if unmatched:
        logger.info(f"Colonnes non reconnues (ignorées) : {unmatched}")
    return df.rename(columns=rename_map)


def validate_required_fields(df: pd.DataFrame) -> None:
    missing = [f for f in REQUIRED_FIELDS if f not in df.columns]
    if missing:
        raise IngestionError(
            f"Champs obligatoires manquants après mapping : {missing}. "
            f"Colonnes disponibles : {list(df.columns)}. "
            f"-> Ajoutez la variante manquante dans config/column_mapping.py"
        )


def _read_ragged_csv(path: Path) -> pd.DataFrame:
    import csv
    with open(path, newline="", encoding="utf-8-sig") as f:
        sample = f.read(4096)
        f.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
        except csv.Error:
            dialect = csv.excel
        rows = list(csv.reader(f, dialect))
    max_cols = max(len(r) for r in rows) if rows else 0
    rows = [r + [None] * (max_cols - len(r)) for r in rows]
    return pd.DataFrame(rows)


def load_file(path: str | Path) -> pd.DataFrame:
    path = Path(path)
    if not path.exists():
        raise IngestionError(f"Fichier introuvable : {path}")

    logger.info(f"Lecture du fichier : {path.name}")

    if path.suffix.lower() in [".xlsx", ".xls"]:
        raw = pd.read_excel(path, header=None, sheet_name=0)
    elif path.suffix.lower() == ".csv":
        raw = _read_ragged_csv(path)
    else:
        raise IngestionError(f"Format de fichier non supporté : {path.suffix}")

    header_row_idx = _detect_header_row(raw)
    df = raw.iloc[header_row_idx + 1:].copy()
    df.columns = raw.iloc[header_row_idx]
    df = df.dropna(how="all").reset_index(drop=True)

    df = standardize_columns(df)
    validate_required_fields(df)

    logger.info(f"Ingestion réussie : {len(df)} lignes, colonnes finales : {list(df.columns)}")
    return df


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage : python ingest.py <chemin_fichier>")
        sys.exit(1)
    print(load_file(sys.argv[1]).head())
