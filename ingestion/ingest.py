"""
Module d'ingestion universelle pour les exports d'accès/comptes.

Logique identique à celle validée sur le projet de gestion des
vulnérabilités : détection automatique de la ligne d'en-tête, gestion des
CSV mal formés, mapping des colonnes vers des noms standards internes.
Seul le référentiel de mapping (config/column_mapping.py) change de domaine.
"""

from __future__ import annotations
import logging
import re
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


def _try_delimited(lines: list[str]) -> pd.DataFrame | None:
    """
    Tentative n°1 : le texte est en fait délimité (virgule, point-virgule,
    tabulation, pipe) mais juste enregistré en .txt plutôt qu'en .csv —
    cas très fréquent (export brut d'un outil, copier-coller de tableur).
    """
    import csv, io

    text = "\n".join(lines)
    if not text.strip():
        return None
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|")
    except csv.Error:
        return None

    rows = list(csv.reader(io.StringIO(text), dialect))
    rows = [r for r in rows if any(cell.strip() for cell in r)]
    if len(rows) < 2 or len(rows[0]) < 2:
        return None

    max_cols = max(len(r) for r in rows)
    rows = [r + [None] * (max_cols - len(r)) for r in rows]
    df = pd.DataFrame(rows)

    best_score = max(_score_header_row(df.iloc[i]) for i in range(min(5, len(df))))
    if best_score <= 0:
        return None
    return df


def _try_fixed_width(lines: list[str]) -> pd.DataFrame | None:
    """
    Tentative n°2 : colonnes alignées par des espaces multiples, typique
    des rapports générés par des outils en ligne de commande ou des
    exports de systèmes legacy (ex. sortie brute d'un annuaire, rapport
    imprimé puis converti en texte).
    """
    rows = [re.split(r"\s{2,}", line.strip()) for line in lines if line.strip()]
    rows = [r for r in rows if len(r) >= 2]
    if len(rows) < 2:
        return None

    max_cols = max(len(r) for r in rows)
    rows = [r + [None] * (max_cols - len(r)) for r in rows]
    df = pd.DataFrame(rows)

    best_score = max(_score_header_row(df.iloc[i]) for i in range(min(5, len(df))))
    if best_score <= 0:
        return None
    return df


def _try_key_value_blocks(raw_text: str) -> pd.DataFrame | None:
    """
    Tentative n°3 : un enregistrement par bloc, séparé par des lignes
    vides, chaque ligne du bloc étant "clé: valeur" ou "clé= valeur"
    (avec ou sans puce). Format courant dans les comptes-rendus,
    fiches individuelles collées dans un document, ou exports type
    "un utilisateur par fiche".

    Exemple reconnu :
        Nom d'utilisateur: jdupont
        Système: Active Directory
        Statut: Actif

        Nom d'utilisateur: mfofana
        Système: CRM
        Statut: Actif
    """
    blocks = re.split(r"\n\s*\n", raw_text.strip())
    line_pattern = re.compile(r"^[-*•]?\s*([^:=]{2,60}?)\s*[:=]\s*(.+)$")

    records = []
    for block in blocks:
        record = {}
        for line in block.split("\n"):
            line = line.strip()
            if not line:
                continue
            match = line_pattern.match(line)
            if match:
                key, value = match.group(1).strip(), match.group(2).strip()
                record[key] = value
        if len(record) >= 2:  # un bloc avec au moins 2 champs a une chance d'être un vrai enregistrement
            records.append(record)

    if len(records) < 1:
        return None

    df = pd.DataFrame(records)
    # Les clés extraites sont déjà les noms de colonnes réels (pas de ligne
    # d'en-tête à détecter séparément) : on vérifie juste qu'au moins une
    # d'entre elles est reconnaissable, pour éviter de valider n'importe
    # quel texte structuré par erreur.
    best_score = _score_header_row(pd.Series(df.columns))
    if best_score <= 0:
        return None
    return df


def _read_txt(path: Path) -> tuple[pd.DataFrame, bool]:
    """
    Lit un fichier .txt en essayant plusieurs interprétations dans l'ordre
    de fiabilité décroissante, jusqu'à ce que l'une d'elles produise un
    résultat exploitable.

    Retourne (dataframe, header_already_named) : le second élément indique
    si les colonnes du DataFrame ont déjà leurs vrais noms (cas des blocs
    clé-valeur) ou si une détection d'en-tête classique reste à faire.
    """
    with open(path, encoding="utf-8-sig", errors="replace") as f:
        raw_text = f.read()

    lines = [l for l in raw_text.splitlines()]
    non_empty_lines = [l for l in lines if l.strip()]

    df = _try_delimited(non_empty_lines)
    if df is not None:
        logger.info("Fichier texte interprété comme des données délimitées.")
        return df, False

    df = _try_fixed_width(non_empty_lines)
    if df is not None:
        logger.info("Fichier texte interprété comme des colonnes alignées par espaces.")
        return df, False

    df = _try_key_value_blocks(raw_text)
    if df is not None:
        logger.info(f"Fichier texte interprété comme {len(df)} bloc(s) clé-valeur.")
        return df, True

    raise IngestionError(
        f"Impossible d'interpréter la structure de {path.name}. "
        "Formats texte reconnus : valeurs délimitées (virgule, point-virgule, "
        "tabulation, pipe), colonnes alignées par des espaces, ou blocs "
        "'clé: valeur' séparés par des lignes vides."
    )


def _read_docx(path: Path) -> tuple[pd.DataFrame, bool]:
    """
    Lit un fichier Word. Essaie d'abord d'y trouver un tableau ; si aucun
    tableau n'est présent, retombe sur les mêmes stratégies de lecture de
    texte libre que pour un .txt, appliquées au texte des paragraphes.
    """
    try:
        from docx import Document
    except ImportError as e:
        raise IngestionError(
            "La bibliothèque 'python-docx' est requise pour lire les fichiers "
            ".docx. Installez-la avec : pip install python-docx"
        ) from e

    doc = Document(str(path))

    if doc.tables:
        best_table_rows, best_score = None, -1
        for table in doc.tables:
            rows = [[cell.text.strip() for cell in row.cells] for row in table.rows]
            if not rows:
                continue
            table_df = pd.DataFrame(rows)
            score = max(_score_header_row(table_df.iloc[i]) for i in range(min(3, len(table_df))))
            if score > best_score:
                best_table_rows, best_score = rows, score

        if best_table_rows is not None:
            logger.info(
                f"{len(doc.tables)} tableau(x) détecté(s) dans le document, "
                f"le plus pertinent a été retenu (score={best_score})."
            )
            max_cols = max(len(r) for r in best_table_rows)
            rows = [r + [None] * (max_cols - len(r)) for r in best_table_rows]
            return pd.DataFrame(rows), False

    # Aucun tableau exploitable : on retombe sur le texte des paragraphes
    logger.info("Aucun tableau exploitable — tentative de lecture en texte libre.")
    paragraphs_with_blanks = [p.text for p in doc.paragraphs]
    non_empty = [p for p in paragraphs_with_blanks if p.strip()]

    df = _try_delimited(non_empty)
    if df is not None:
        logger.info("Contenu du document interprété comme des données délimitées.")
        return df, False

    df = _try_fixed_width(non_empty)
    if df is not None:
        logger.info("Contenu du document interprété comme des colonnes alignées.")
        return df, False

    df = _try_key_value_blocks("\n".join(paragraphs_with_blanks))
    if df is not None:
        logger.info(f"Contenu du document interprété comme {len(df)} bloc(s) clé-valeur.")
        return df, True

    raise IngestionError(
        f"Aucun tableau ni structure de données reconnaissable dans {path.name}. "
        "Formats reconnus : tableau Word, texte délimité, colonnes alignées, "
        "ou blocs 'clé: valeur' séparés par des lignes vides."
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

    header_already_named = False

    if path.suffix.lower() in [".xlsx", ".xls"]:
        raw = pd.read_excel(path, header=None, sheet_name=0)
    elif path.suffix.lower() == ".csv":
        raw = _read_ragged_csv(path)
    elif path.suffix.lower() == ".docx":
        raw, header_already_named = _read_docx(path)
    elif path.suffix.lower() == ".txt":
        raw, header_already_named = _read_txt(path)
    else:
        raise IngestionError(
            f"Format de fichier non supporté : {path.suffix}. "
            f"Formats acceptés : .csv, .xlsx, .xls, .docx, .txt"
        )

    if header_already_named:
        # Les colonnes portent déjà leurs vrais noms (cas des blocs clé-valeur)
        df = raw.reset_index(drop=True)
    else:
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
