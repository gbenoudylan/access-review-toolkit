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


def _detect_encoding(path: Path) -> str:
    """
    Détecte l'encodage texte d'un fichier plutôt que d'imposer l'UTF-8.

    Beaucoup d'exports réels (notamment depuis Excel ou des outils Windows)
    sont en Windows-1252/Latin-1, pas en UTF-8 — les lire en UTF-8 strict
    échoue ou corrompt les caractères accentués. On essaie plusieurs
    encodages courants dans l'ordre et on garde le premier qui décode le
    fichier sans erreur ; Latin-1 en dernier recours ne lève jamais
    d'erreur (il associe un caractère à chaque octet), donc la fonction
    retourne toujours un encodage utilisable.
    """
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            raw.decode(encoding)
            return encoding
        except UnicodeDecodeError:
            continue
    return "latin-1"  # filet de sécurité théorique, jamais atteint en pratique


def _normalize(text: str) -> str:
    return str(text).strip().lower().replace("_", " ").replace("-", " ")


def _score_header_row(row: pd.Series, column_mapping: dict = None) -> int:
    column_mapping = column_mapping or COLUMN_MAPPING
    all_variants = {
        _normalize(v) for variants in column_mapping.values() for v in variants
    }
    score = 0
    for cell in row:
        if pd.isna(cell):
            continue
        if _normalize(cell) in all_variants:
            score += 1
    return score


def _detect_header_row(raw: pd.DataFrame, column_mapping: dict = None, max_scan_rows: int = 15) -> int:
    best_row, best_score = 0, -1
    for i in range(min(max_scan_rows, len(raw))):
        score = _score_header_row(raw.iloc[i], column_mapping)
        if score > best_score:
            best_row, best_score = i, score
    if best_score <= 0:
        logger.warning("Aucune ligne d'en-tête reconnue, utilisation de la ligne 0.")
        return 0
    logger.info(f"En-tête détecté à la ligne {best_row} (score={best_score}).")
    return best_row


def _match_column(col_name: str, column_mapping: dict = None, threshold: int = 85) -> str | None:
    column_mapping = column_mapping or COLUMN_MAPPING
    col_norm = _normalize(col_name)

    for standard_name, variants in column_mapping.items():
        if col_norm in [_normalize(v) for v in variants]:
            return standard_name

    if _HAS_RAPIDFUZZ:
        best_field, best_score = None, 0
        for standard_name, variants in column_mapping.items():
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


def _synthesize_full_name(df: pd.DataFrame) -> pd.DataFrame:
    """
    Certains exports ne fournissent le nom qu'en deux colonnes séparées
    ('First Name' / 'Last Name'), sans colonne 'Display Name'/'Full Name'
    unique. Dans ce cas, on reconstitue 'full_name' automatiquement plutôt
    que de le laisser absent — sans jamais écraser un 'full_name' déjà
    présent par ailleurs.
    """
    has_first_last = "first_name" in df.columns and "last_name" in df.columns
    if not has_first_last:
        return df

    synthesized = (
        df["first_name"].fillna("").astype(str).str.strip()
        + " "
        + df["last_name"].fillna("").astype(str).str.strip()
    ).str.strip()

    if "full_name" in df.columns:
        df["full_name"] = df["full_name"].combine_first(synthesized.replace("", None))
    else:
        df["full_name"] = synthesized
        logger.info("'full_name' reconstitué à partir de 'first_name' + 'last_name'.")

    return df


def standardize_columns(df: pd.DataFrame, column_mapping: dict = None) -> pd.DataFrame:
    """
    Renomme les colonnes reconnues vers leur nom standard.

    Il arrive qu'un même export contienne deux colonnes distinctes qui
    correspondent au même champ standard (ex. 'SAM Account Name' et
    'Logon Name' pointent toutes deux vers 'username' dans un export AD
    classique). Les mapper telles quelles produirait deux colonnes de même
    nom après renommage — invalide pour pandas/Arrow en aval (l'affichage
    Streamlit, notamment, lève une erreur sur des noms de colonnes
    dupliqués). On fusionne donc ces cas : la première colonne rencontrée
    fait foi, complétée par les valeurs non vides de la seconde là où elle
    a des trous, puis la seconde est supprimée.
    """
    rename_map, unmatched = {}, []
    claimed_by: dict[str, str] = {}  # nom standard -> colonne originale déjà utilisée

    # Deux colonnes brutes peuvent porter EXACTEMENT le même libellé (pas
    # juste équivalent) — ex. un export avec deux colonnes "Status". Dans
    # ce cas, df["Status"] renvoie les deux à la fois sous forme de
    # DataFrame (pas une Series), ce qui casse silencieusement la fusion
    # ci-dessous. On rend donc les libellés bruts uniques (par position)
    # avant tout traitement, pour que chaque colonne soit toujours
    # adressable individuellement.
    if df.columns.duplicated().any():
        seen: dict = {}
        new_labels = []
        for col in df.columns:
            key = str(col)
            seen[key] = seen.get(key, 0) + 1
            new_labels.append(col if seen[key] == 1 else f"{col}__dup{seen[key]}")
        logger.info(
            f"Colonnes brutes en double détectées (même libellé exact) : "
            f"renommées temporairement par position avant fusion."
        )
        df = df.copy()
        df.columns = new_labels

    for col in df.columns:
        # Retire le suffixe temporaire ('__dupN') avant reconnaissance,
        # sans quoi il empêche le fuzzy matching de reconnaître la colonne.
        lookup_name = re.sub(r"__dup\d+$", "", str(col))
        matched = _match_column(lookup_name, column_mapping)
        if not matched:
            unmatched.append(col)
            continue
        if matched not in claimed_by:
            claimed_by[matched] = col
            rename_map[col] = matched
        else:
            primary_col = claimed_by[matched]
            df[primary_col] = df[primary_col].combine_first(df[col])
            df = df.drop(columns=[col])
            logger.info(
                f"Colonne '{col}' fusionnée dans '{primary_col}' (toutes deux -> '{matched}')."
            )

    if unmatched:
        logger.info(f"Colonnes non reconnues (ignorées) : {unmatched}")
    return df.rename(columns=rename_map)


def validate_required_fields(df: pd.DataFrame, required_fields: list = None) -> None:
    required_fields = required_fields if required_fields is not None else REQUIRED_FIELDS
    missing = [f for f in required_fields if f not in df.columns]
    if missing:
        raise IngestionError(
            f"Champs obligatoires manquants après mapping : {missing}. "
            f"Colonnes disponibles : {list(df.columns)}. "
            f"-> Ajoutez la variante manquante dans config/column_mapping.py"
        )

    # La colonne peut exister tout en étant entièrement vide — ex. un
    # mapping qui a reconnu la bonne étiquette de colonne mais où les
    # vraies valeurs se trouvent ailleurs (erreur de structure du
    # fichier). Un champ obligatoire présent mais 100% vide n'est pas
    # plus exploitable qu'un champ absent, et produirait un rapport
    # rempli de comptes anonymes sans le moindre avertissement.
    if len(df) > 0:
        empty_required = [
            f for f in required_fields
            if df[f].isna().all() or (df[f].astype(str).str.strip() == "").all()
        ]
        if empty_required:
            raise IngestionError(
                f"Champ(s) obligatoire(s) présent(s) mais entièrement vide(s) : "
                f"{empty_required}. La colonne existe dans le fichier mais ne "
                f"contient aucune valeur exploitable — vérifiez que le bon "
                f"champ source a été reconnu (une colonne mal alignée ou une "
                f"ligne d'en-tête incorrectement détectée en est souvent la cause)."
            )


def _read_ragged_csv(path: Path) -> pd.DataFrame:
    import csv
    with open(path, newline="", encoding=_detect_encoding(path)) as f:
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


def _try_delimited(lines: list[str], column_mapping: dict = None) -> pd.DataFrame | None:
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

    best_score = max(_score_header_row(df.iloc[i], column_mapping) for i in range(min(5, len(df))))
    if best_score <= 0:
        return None
    return df


def _try_fixed_width(lines: list[str], column_mapping: dict = None) -> pd.DataFrame | None:
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

    best_score = max(_score_header_row(df.iloc[i], column_mapping) for i in range(min(5, len(df))))
    if best_score <= 0:
        return None
    return df


def _try_key_value_blocks(raw_text: str, column_mapping: dict = None) -> pd.DataFrame | None:
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
    best_score = _score_header_row(pd.Series(df.columns), column_mapping)
    if best_score <= 0:
        return None
    return df


def _read_txt(path: Path, column_mapping: dict = None) -> tuple[pd.DataFrame, bool]:
    """
    Lit un fichier .txt en essayant plusieurs interprétations dans l'ordre
    de fiabilité décroissante, jusqu'à ce que l'une d'elles produise un
    résultat exploitable.

    Avant cela, si le fichier contient plusieurs blocs séparés par une
    ligne vide, chacun se comportant comme un tableau délimité/aligné à
    part entière (typiquement : un bloc "identités", un bloc "rôles"
    séparé, pour les mêmes comptes — ou au contraire un bloc par système),
    chaque bloc est d'abord traité indépendamment, puis fusionné par
    colonne ou empilé selon le même principe que pour Excel/Word/ZIP. Sans
    quoi l'en-tête du second bloc se retrouvait traité comme une donnée,
    et ses valeurs glissaient dans les mauvaises colonnes.

    Retourne (dataframe, header_already_named) : le second élément indique
    si les colonnes du DataFrame ont déjà leurs vrais noms (cas des blocs
    clé-valeur) ou si une détection d'en-tête classique reste à faire.
    """
    with open(path, encoding=_detect_encoding(path), errors="replace") as f:
        raw_text = f.read()

    raw_blocks = [b for b in re.split(r"\n\s*\n", raw_text.strip()) if b.strip()]
    if len(raw_blocks) >= 2:
        named_blocks = {}
        for i, block in enumerate(raw_blocks, 1):
            block_lines = [l for l in block.splitlines() if l.strip()]
            block_df = _try_delimited(block_lines, column_mapping)
            if block_df is None:
                block_df = _try_fixed_width(block_lines, column_mapping)
            if block_df is None:
                named_blocks = {}
                break  # un bloc ne correspond à aucune des 2 stratégies -> on abandonne cette voie
            header_row_idx = _detect_header_row(block_df, column_mapping)
            standardized = block_df.iloc[header_row_idx + 1:].copy()
            standardized.columns = block_df.iloc[header_row_idx]
            standardized = standardized.dropna(how="all").reset_index(drop=True)
            standardized = standardize_columns(standardized, column_mapping)
            named_blocks[f"Bloc {i}"] = standardized

        if named_blocks:
            logger.info(f"Fichier texte interprété comme {len(named_blocks)} bloc(s) tabulaire(s) distinct(s).")
            result = _merge_or_stack_named_tables(named_blocks, None, allow_name_as_system=False)
            return result, True

    lines = [l for l in raw_text.splitlines()]
    non_empty_lines = [l for l in lines if l.strip()]

    df = _try_delimited(non_empty_lines, column_mapping)
    if df is not None:
        logger.info("Fichier texte interprété comme des données délimitées.")
        return df, False

    df = _try_fixed_width(non_empty_lines, column_mapping)
    if df is not None:
        logger.info("Fichier texte interprété comme des colonnes alignées par espaces.")
        return df, False

    df = _try_key_value_blocks(raw_text, column_mapping)
    if df is not None:
        logger.info(f"Fichier texte interprété comme {len(df)} bloc(s) clé-valeur.")
        return df, True

    raise IngestionError(
        f"Impossible d'interpréter la structure de {path.name}. "
        "Formats texte reconnus : valeurs délimitées (virgule, point-virgule, "
        "tabulation, pipe), colonnes alignées par des espaces, ou blocs "
        "'clé: valeur' séparés par des lignes vides."
    )


def _read_docx(path: Path, column_mapping: dict = None) -> tuple[pd.DataFrame, bool]:
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
        # Un document peut contenir plusieurs tableaux légitimes, dans deux
        # cas de figure différents : soit chaque tableau décrit des comptes
        # DIFFÉRENTS (ex. un système par tableau, à empiler), soit les
        # tableaux décrivent les MÊMES comptes avec des colonnes différentes
        # (ex. un tableau "Identités", un tableau "Rôles" séparé — à
        # fusionner par colonne). Traiter chaque tableau indépendamment
        # (détection d'en-tête propre) puis laisser _merge_or_stack_named_
        # tables trancher, plutôt que de supposer que tous les tableaux
        # partagent le même nombre de colonnes (ce qui ferait perdre
        # silencieusement tout tableau à structure différente).
        table_dfs: dict = {}
        for idx, table in enumerate(doc.tables, 1):
            rows = [[cell.text.strip() for cell in row.cells] for row in table.rows]
            if not rows:
                continue
            table_df_raw = pd.DataFrame(rows)
            try:
                header_row_idx = _detect_header_row(table_df_raw, column_mapping)
            except IngestionError:
                logger.warning(f"Tableau {idx} ignoré : aucun en-tête reconnaissable.")
                continue
            table_df = table_df_raw.iloc[header_row_idx + 1:].copy()
            table_df.columns = table_df_raw.iloc[header_row_idx]
            table_df = table_df.dropna(how="all").reset_index(drop=True)
            table_df = standardize_columns(table_df, column_mapping)
            table_dfs[f"Table {idx}"] = table_df

        if table_dfs:
            result = _merge_or_stack_named_tables(table_dfs, None, allow_name_as_system=False)
            logger.info(f"{len(doc.tables)} tableau(x) détecté(s) dans le document.")
            return result, True

    # Aucun tableau exploitable : on retombe sur le texte des paragraphes
    logger.info("Aucun tableau exploitable — tentative de lecture en texte libre.")
    paragraphs_with_blanks = [p.text for p in doc.paragraphs]
    non_empty = [p for p in paragraphs_with_blanks if p.strip()]

    df = _try_delimited(non_empty, column_mapping)
    if df is not None:
        logger.info("Contenu du document interprété comme des données délimitées.")
        return df, False

    df = _try_fixed_width(non_empty, column_mapping)
    if df is not None:
        logger.info("Contenu du document interprété comme des colonnes alignées.")
        return df, False

    df = _try_key_value_blocks("\n".join(paragraphs_with_blanks), column_mapping)
    if df is not None:
        logger.info(f"Contenu du document interprété comme {len(df)} bloc(s) clé-valeur.")
        return df, True

    raise IngestionError(
        f"Aucun tableau ni structure de données reconnaissable dans {path.name}. "
        "Formats reconnus : tableau Word, texte délimité, colonnes alignées, "
        "ou blocs 'clé: valeur' séparés par des lignes vides."
    )



def _read_json(path: Path) -> pd.DataFrame:
    """
    Lit un fichier JSON. Accepte :
        - une liste d'objets : [{"username": "...", ...}, ...]
        - un objet unique contenant une liste sous une clé courante
          (results, data, users, accounts, records, items, value)
        - un objet unique représentant un seul compte
    """
    import json

    with open(path, encoding=_detect_encoding(path)) as f:
        data = json.load(f)

    if isinstance(data, list):
        records = data
    elif isinstance(data, dict):
        list_keys = ["results", "data", "users", "accounts", "records", "items", "value"]
        records = None
        for key in list_keys:
            if key in data and isinstance(data[key], list):
                records = data[key]
                break
        if records is None:
            # objet unique = un seul enregistrement
            records = [data]
    else:
        raise IngestionError(f"Structure JSON non reconnue dans {path.name}.")

    if not records:
        raise IngestionError(f"Aucun enregistrement trouvé dans {path.name}.")

    # Aplatit les dictionnaires imbriqués simples (ex. {"user": {"name": "..."}})
    df = pd.json_normalize(records, sep="_")
    return df


def _read_xml(path: Path) -> pd.DataFrame:
    """
    Lit un fichier XML. Suppose une structure répétitive classique
    (une balise par enregistrement, ex. <user>...</user> ou <account>...</account>),
    avec les champs en sous-balises ou en attributs.
    """
    try:
        df = pd.read_xml(path)
    except Exception as e:
        raise IngestionError(
            f"Impossible d'interpréter la structure XML de {path.name} ({e}). "
            "Le fichier doit contenir des éléments répétitifs représentant "
            "chacun un compte (ex. <user>...</user>)."
        ) from e

    if df.empty:
        raise IngestionError(f"Aucun enregistrement exploitable trouvé dans {path.name}.")
    return df


def _read_html(path: Path, column_mapping: dict = None) -> pd.DataFrame:
    """
    Lit un fichier HTML contenant un ou plusieurs tableaux (ex. export copié
    depuis une page web/intranet). Garde le tableau le plus pertinent, même
    logique que pour les tableaux Word multiples.

    pandas détecte déjà l'en-tête via les balises <th>/<thead> : on
    reconstruit une forme "brute" (en-tête recollé comme première ligne)
    pour rester cohérent avec les autres lecteurs et laisser la détection
    d'en-tête standard s'appliquer une seule fois, au bon endroit.
    """
    try:
        tables = pd.read_html(path)
    except ValueError as e:
        raise IngestionError(f"Aucun tableau trouvé dans {path.name} ({e}).") from e

    best_raw_rows, best_score = None, -1
    for table_df in tables:
        raw_rows = [list(table_df.columns)] + table_df.astype(object).values.tolist()
        candidate = pd.DataFrame(raw_rows)
        score = max(_score_header_row(candidate.iloc[i], column_mapping) for i in range(min(3, len(candidate))))
        if score > best_score:
            best_raw_rows, best_score = raw_rows, score

    if best_raw_rows is None:
        raise IngestionError(f"Aucun tableau exploitable trouvé dans {path.name}.")

    logger.info(f"{len(tables)} tableau(x) HTML détecté(s), le plus pertinent retenu (score={best_score}).")
    return pd.DataFrame(best_raw_rows)


_UAC_ACCOUNTDISABLE_BIT = 0x2  # bit standard Active Directory pour "compte désactivé"


def _read_ldif(path: Path) -> pd.DataFrame:
    """
    Lit un fichier LDIF (export natif LDAP/Active Directory).

    Structure LDIF : des entrées séparées par des lignes vides, chaque
    ligne étant "attribut: valeur" (ou "attribut:: valeur_base64" pour les
    valeurs encodées). Un attribut peut apparaître plusieurs fois (valeurs
    multiples) — on garde alors la première occurrence pour rester simple.

    Cas particulier traité : 'userAccountControl' est un bitmask numérique
    (pas un statut texte). On le décode ici pour en tirer directement un
    statut Active/Disabled exploitable par le reste du pipeline.
    """
    with open(path, encoding=_detect_encoding(path), errors="replace") as f:
        raw_text = f.read()

    # Les lignes de continuation LDIF commencent par un espace : elles
    # prolongent la ligne précédente et doivent être recollées avant parsing.
    unfolded_lines = []
    for line in raw_text.splitlines():
        if line.startswith(" ") and unfolded_lines:
            unfolded_lines[-1] += line[1:]
        else:
            unfolded_lines.append(line)

    entries_text = re.split(r"\n\s*\n", "\n".join(unfolded_lines))
    records = []

    for entry_text in entries_text:
        record = {}
        for line in entry_text.split("\n"):
            line = line.rstrip()
            if not line or line.startswith("#"):
                continue
            # "attribut:: valeur" = base64, on ignore le décodage (rare
            # pour les champs texte qui nous intéressent ici)
            match = re.match(r"^([\w;-]+)::?\s*(.*)$", line)
            if not match:
                continue
            key, value = match.group(1), match.group(2).strip()
            if key.lower() == "useraccountcontrol":
                try:
                    flags = int(value)
                    value = "Disabled" if (flags & _UAC_ACCOUNTDISABLE_BIT) else "Active"
                except ValueError:
                    pass
            if key not in record:  # garde la première valeur si attribut répété
                record[key] = value
        if record:
            records.append(record)

    if not records:
        raise IngestionError(
            f"Aucune entrée LDIF exploitable trouvée dans {path.name}. "
            "Format attendu : entrées séparées par des lignes vides, "
            "lignes 'attribut: valeur'."
        )

    # Un export LDIF représente par nature un seul système (l'annuaire
    # LDAP/AD lui-même) : il n'y a jamais de colonne "système" explicite
    # dans les données, contrairement à un export multi-systèmes. On
    # l'ajoute donc nous-mêmes plutôt que d'échouer sur un champ obligatoire
    # qui n'a structurellement aucune raison d'exister dans ce format.
    for record in records:
        record.setdefault("system", "Active Directory / LDAP")

    logger.info(f"{len(records)} entrée(s) LDIF décodée(s).")
    return pd.DataFrame(records)


def _read_pdf(path: Path, column_mapping: dict = None) -> pd.DataFrame:
    """
    Extrait un ou plusieurs tableaux depuis un PDF, sur l'ensemble de ses
    pages, en distinguant deux situations bien différentes plutôt que de
    se fier au seul nombre de colonnes (ce qui confondait à tort deux
    tableaux différents ayant par coïncidence le même nombre de colonnes) :

    1. Un même tableau logique étalé sur plusieurs pages (cas fréquent :
       un export de 800 lignes ne tient jamais sur une seule page, l'en-
       tête étant parfois répété en haut de chaque page) -> les fragments
       partageant exactement les mêmes colonnes, une fois standardisées,
       sont regroupés et concaténés, en écartant les répétitions d'en-tête.
    2. Plusieurs tableaux réellement différents (colonnes différentes) au
       sein du même PDF -> traités comme pour Excel/Word/ZIP/TXT : fusion
       par colonne si les comptes se recouvrent (mêmes comptes, attributs
       différents), empilement sinon (comptes/systèmes distincts).
    """
    try:
        import pdfplumber
    except ImportError as e:
        raise IngestionError(
            "La bibliothèque 'pdfplumber' est requise pour lire les PDF. "
            "Installez-la avec : pip install pdfplumber"
        ) from e

    all_tables = []  # toutes les tables trouvées, toutes pages confondues, dans l'ordre
    with pdfplumber.open(str(path)) as pdf:
        for page in pdf.pages:
            tables = page.extract_tables()
            if not tables:
                logger.warning(
                    "Aucune bordure de tableau détectée sur une page : tentative par "
                    "alignement du texte, moins fiable (peut mal découper "
                    "des colonnes ou des lignes) — vérifiez le résultat."
                )
                tables = page.extract_tables(
                    table_settings={
                        "vertical_strategy": "text",
                        "horizontal_strategy": "text",
                    }
                )
            all_tables.extend(t for t in tables if t)

    if not all_tables:
        raise IngestionError(
            f"Aucun tableau détecté dans {path.name}. L'extraction de tableaux "
            "PDF est fiable uniquement si le PDF contient une vraie grille "
            "(pas une image scannée ni une mise en page libre)."
        )

    # 1) Parcourir les fragments dans l'ordre des pages, en gardant trace du
    #    tableau "actif" : une page sans en-tête reconnaissable est une
    #    continuation du tableau précédent (cas normal — l'en-tête n'est
    #    souvent présent qu'une fois, en page 1) ; une page dont la première
    #    ligne EST un en-tête reconnaissable démarre soit une répétition du
    #    même tableau (même signature de colonnes -> ligne ignorée), soit un
    #    tableau réellement différent (signature différente -> nouveau
    #    groupe). Une approche par simple nombre de colonnes confondait à
    #    tort deux tableaux différents ayant coïncidemment le même nombre
    #    de colonnes ; une détection d'en-tête indépendante par fragment
    #    cassait, elle, le cas normal où l'en-tête n'apparaît qu'en page 1.
    HEADER_SCORE_THRESHOLD = 2  # score jugé "suffisamment reconnaissable" pour être un en-tête

    groups: list = []  # liste de dicts {signature, columns, rows: [...]}
    current_group = None

    # On traite chaque LIGNE dans l'ordre, tous fragments/pages confondus
    # (pas fragment par fragment) : un en-tête peut réapparaître au milieu
    # d'une page, pas seulement en haut de chaque nouvelle page (ex. quand
    # le PDF source assemble plusieurs petits tableaux qui ne s'alignent
    # pas avec les sauts de page).
    for table in all_tables:
        for row in table:
            row_score = _score_header_row(pd.Series(row), column_mapping)
            if row_score >= HEADER_SCORE_THRESHOLD:
                mapped_columns = [_match_column(c, column_mapping) or str(c) for c in row]
                signature = frozenset(mapped_columns)
                if current_group is not None and signature == current_group["signature"]:
                    continue  # répétition de l'en-tête du tableau en cours : ignorée
                current_group = {"signature": signature, "columns": mapped_columns, "rows": []}
                groups.append(current_group)
                continue

            if current_group is None:
                continue  # donnée avant tout en-tête reconnaissable : ignorée
            if len(row) == len(current_group["columns"]):
                current_group["rows"].append(row)

    if not groups:
        raise IngestionError(f"Aucun en-tête reconnaissable dans les tableaux de {path.name}.")

    named_tables: dict = {}
    for i, group in enumerate(groups, 1):
        if not group["rows"]:
            continue
        group_df = pd.DataFrame(group["rows"], columns=group["columns"])
        group_df = standardize_columns(group_df, column_mapping)
        key = f"Tableau {i}"
        if key in named_tables:
            named_tables[key] = pd.concat([named_tables[key], group_df], ignore_index=True)
        else:
            named_tables[key] = group_df

    logger.info(
        f"{len(all_tables)} fragment(s) de page(s) regroupés en {len(named_tables)} "
        f"tableau(x) distinct(s) par signature de colonnes."
    )

    # 2) Fusion par colonne (comptes communs) ou empilement (comptes
    #    distincts) entre tableaux de signatures différentes.
    return _merge_or_stack_named_tables(named_tables, None, allow_name_as_system=False)


# Extensions traitées nativement par _load_single_file (utilisé aussi par
# _read_zip pour savoir quels fichiers internes tenter d'ouvrir).
SUPPORTED_EXTENSIONS = [
    ".csv", ".xlsx", ".xls", ".docx", ".txt", ".json", ".xml", ".html", ".htm",
    ".ldif", ".pdf", ".jpeg", ".jpg", ".png",
]


def _read_image_ocr(path: Path, column_mapping: dict = None) -> pd.DataFrame:
    """
    Extrait un tableau depuis une image (capture d'écran, photo) par
    reconnaissance optique de caractères (OCR).

    À la différence de tous les autres formats de ce module, l'OCR ne lit
    pas une structure de données fiable : il DEVINE du texte à partir de
    pixels. Colonnes mal alignées, chiffres confondus (0/O, 1/l), lignes
    fusionnées ou coupées sont des erreurs connues et fréquentes,
    particulièrement sur un tableau dense (beaucoup de colonnes serrées,
    comme un export de comptes). Le résultat n'est donc jamais traité avec
    la même confiance qu'un fichier structuré : chaque ligne produite est
    marquée '_ocr_source' = True, pour que les rapports générés affichent
    un avertissement explicite invitant à une vérification manuelle.
    """
    try:
        import pytesseract
        from PIL import Image
    except ImportError as e:
        raise IngestionError(
            "Les bibliothèques 'pytesseract' et 'Pillow' sont requises pour lire "
            "les images. Installez-les avec : pip install pytesseract Pillow "
            "(et le binaire tesseract-ocr doit être installé sur le système)."
        ) from e

    image = Image.open(path)
    raw_text = pytesseract.image_to_string(image)

    if not raw_text.strip():
        raise IngestionError(
            f"Aucun texte reconnu dans {path.name} par OCR. L'image est peut-être "
            "trop floue, trop petite, ou ne contient pas de texte exploitable."
        )

    lines = [l for l in raw_text.splitlines() if l.strip()]
    df = _try_delimited(lines, column_mapping)
    strategy = "délimité"
    if df is None:
        df = _try_fixed_width(lines, column_mapping)
        strategy = "colonnes alignées"

    if df is None:
        raise IngestionError(
            f"Texte reconnu par OCR dans {path.name}, mais aucune structure de "
            "tableau exploitable n'a pu en être dégagée. L'OCR fonctionne mieux "
            "sur des tableaux à bordures nettes et peu de colonnes ; envisagez "
            "de fournir directement l'export source (Excel, CSV, PDF...) plutôt "
            "qu'une capture d'écran."
        )

    header_row_idx = _detect_header_row(df, column_mapping)
    result = df.iloc[header_row_idx + 1:].copy()
    result.columns = df.iloc[header_row_idx]
    result = result.dropna(how="all").reset_index(drop=True)
    result = standardize_columns(result, column_mapping)
    result["_ocr_source"] = True

    logger.warning(
        f"'{path.name}' lu par OCR (stratégie : {strategy}) — {len(result)} ligne(s) "
        f"extraite(s). Fiabilité inférieure à un fichier structuré : à vérifier "
        f"manuellement avant toute décision."
    )
    return result


def _merge_or_stack_named_tables(
    named_dfs: dict, default_system: str | None, allow_name_as_system: bool,
) -> pd.DataFrame:
    """
    Logique commune (Excel multi-feuilles, ZIP multi-fichiers, Word
    multi-tableaux) : décide, table par table, s'il faut FUSIONNER par
    colonne (mêmes comptes, attributs différents — jointure sur username)
    ou EMPILER (comptes différents — ex. un système par table).

    La décision se fait par le recouvrement réel des comptes ('username')
    entre tables : un fort recouvrement (>= 50% des comptes de la plus
    petite table présents dans l'autre) indique une fusion par colonne ;
    un recouvrement faible ou nul indique des comptes/systèmes distincts
    à empiler. `allow_name_as_system` doit rester False quand la source
    n'a qu'un seul élément par nature (géré différemment par l'appelant).
    """
    names = list(named_dfs.keys())
    parent = {n: n for n in names}

    def find(n):
        while parent[n] != n:
            n = parent[n]
        return n

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for i, name_a in enumerate(names):
        df_a = named_dfs[name_a]
        if "username" not in df_a.columns:
            continue
        keys_a = set(df_a["username"].dropna())
        if not keys_a:
            continue
        for name_b in names[i + 1:]:
            df_b = named_dfs[name_b]
            if "username" not in df_b.columns:
                continue
            keys_b = set(df_b["username"].dropna())
            if not keys_b:
                continue
            overlap = len(keys_a & keys_b) / min(len(keys_a), len(keys_b))
            if overlap >= 0.5:
                union(name_a, name_b)
                logger.info(
                    f"'{name_a}' et '{name_b}' fusionnées par colonne "
                    f"(recouvrement de comptes : {overlap:.0%})."
                )

    groups: dict = {}
    for n in names:
        groups.setdefault(find(n), []).append(n)

    processed = []
    for root, members in groups.items():
        if len(members) == 1:
            group_df = named_dfs[members[0]]
            if "system" not in group_df.columns:
                if default_system:
                    group_df = group_df.copy()
                    group_df["system"] = default_system
                elif allow_name_as_system and len(named_dfs) > 1:
                    group_df = group_df.copy()
                    group_df["system"] = members[0]
        else:
            group_df = named_dfs[members[0]]
            for other_name in members[1:]:
                other_df = named_dfs[other_name]
                group_df = group_df.merge(
                    other_df, on="username", how="outer", suffixes=("", "_dup")
                )
                dup_cols = [c for c in group_df.columns if str(c).endswith("_dup")]
                for dup_col in dup_cols:
                    base_col = str(dup_col)[:-4]
                    if base_col in group_df.columns:
                        group_df[base_col] = group_df[base_col].combine_first(group_df[dup_col])
                    else:
                        group_df[base_col] = group_df[dup_col]
                group_df = group_df.drop(columns=dup_cols)
            if "system" not in group_df.columns and default_system:
                group_df["system"] = default_system
        processed.append(group_df)

    logger.info(
        f"{len(named_dfs)} élément(s) source, regroupés en {len(processed)} bloc(s) "
        f"après fusion/empilement."
    )
    return pd.concat(processed, ignore_index=True)


def _read_excel_all_sheets(
    path: Path, column_mapping: dict = None, default_system: str | None = None,
) -> pd.DataFrame:
    """
    Lit TOUTES les feuilles d'un classeur Excel, pas seulement la première,
    en distinguant deux cas de figure bien différents :

    1. Chaque feuille décrit des comptes DIFFÉRENTS (ex. une feuille par
       système) -> les feuilles sont empilées (concaténées), et le nom de
       chaque feuille sert de valeur par défaut pour 'system'.
    2. Les feuilles décrivent les MÊMES comptes mais avec des colonnes
       différentes (ex. une feuille "Identités" avec noms/dates de
       connexion, une feuille "Rôles" avec les habilitations) -> les
       empiler produirait des lignes à moitié vides pour chaque compte ;
       il faut au contraire les FUSIONNER par colonne (jointure sur
       'username'), pour obtenir un enregistrement complet par compte.

    Voir _merge_or_stack_named_tables pour la logique de décision.
    """
    sheets = pd.read_excel(path, header=None, sheet_name=None)
    sheet_dfs: dict = {}
    for sheet_name, raw in sheets.items():
        raw = raw.dropna(how="all")
        if raw.empty:
            continue
        try:
            header_row_idx = _detect_header_row(raw, column_mapping)
        except IngestionError:
            logger.warning(f"Feuille '{sheet_name}' ignorée : aucun en-tête reconnaissable.")
            continue
        sheet_df = raw.iloc[header_row_idx + 1:].copy()
        sheet_df.columns = raw.iloc[header_row_idx]
        sheet_df = sheet_df.dropna(how="all").reset_index(drop=True)
        sheet_df = standardize_columns(sheet_df, column_mapping)
        sheet_dfs[str(sheet_name)] = sheet_df

    if not sheet_dfs:
        raise IngestionError(f"Aucune feuille exploitable trouvée dans {path.name}.")

    return _merge_or_stack_named_tables(sheet_dfs, default_system, allow_name_as_system=True)


def _load_single_file(
    path: Path, column_mapping: dict = None, required_fields: list = None,
    default_system: str | None = None, _defer_finalize: bool = False,
) -> pd.DataFrame:
    """
    Charge un unique fichier (tous formats sauf .zip) et retourne un
    DataFrame standardisé.

    `_defer_finalize` (usage interne, par _read_zip) : quand True, ignore
    le nom de fichier comme repli pour 'system' et saute la validation des
    champs obligatoires — l'appelant s'en charge lui-même après avoir
    éventuellement fusionné plusieurs fichiers par colonne (voir
    _merge_or_stack_named_tables).
    """
    logger.info(f"Lecture du fichier : {path.name}")

    header_already_named = False
    suffix = path.suffix.lower()

    if suffix in [".xlsx", ".xls"]:
        df = _read_excel_all_sheets(path, column_mapping, default_system)
        df = _synthesize_full_name(df)
        if _defer_finalize:
            return df
        effective_required = required_fields if required_fields is not None else REQUIRED_FIELDS
        if "system" not in df.columns and "system" in effective_required:
            resolved_system = default_system or path.stem
            df["system"] = resolved_system
        validate_required_fields(df, required_fields)
        logger.info(f"Ingestion réussie : {len(df)} lignes, colonnes finales : {list(df.columns)}")
        return df
    elif suffix == ".csv":
        raw = _read_ragged_csv(path)
    elif suffix == ".docx":
        raw, header_already_named = _read_docx(path, column_mapping)
    elif suffix == ".txt":
        raw, header_already_named = _read_txt(path, column_mapping)
    elif suffix == ".json":
        raw = _read_json(path)
        header_already_named = True
    elif suffix == ".xml":
        raw = _read_xml(path)
        header_already_named = True
    elif suffix in [".html", ".htm"]:
        raw = _read_html(path, column_mapping)
    elif suffix == ".ldif":
        raw = _read_ldif(path)
        header_already_named = True
    elif suffix == ".pdf":
        raw = _read_pdf(path, column_mapping)
        header_already_named = True
    elif suffix in [".jpeg", ".jpg", ".png"]:
        raw = _read_image_ocr(path, column_mapping)
        header_already_named = True
    else:
        raise IngestionError(
            f"Format de fichier non supporté : {path.suffix}. "
            f"Formats acceptés : {', '.join(SUPPORTED_EXTENSIONS)}, .zip"
        )

    if header_already_named:
        df = raw.reset_index(drop=True)
    else:
        header_row_idx = _detect_header_row(raw, column_mapping)
        df = raw.iloc[header_row_idx + 1:].copy()
        df.columns = raw.iloc[header_row_idx]
        df = df.dropna(how="all").reset_index(drop=True)

    df = standardize_columns(df, column_mapping)
    df = _synthesize_full_name(df)

    if _defer_finalize:
        return df

    # Un export "brut" d'un seul système (ex. extraction Active Directory
    # pure) ne contient souvent aucune colonne identifiant le système lui-
    # même : cette information est implicite (tout le fichier = ce système),
    # pas une donnée par ligne. Plutôt que d'échouer, on comble ce vide :
    # priorité au nom explicite passé à l'appel (ex. depuis le dashboard),
    # sinon repli automatique sur le nom du fichier — jamais d'échec pour
    # cette seule raison. Uniquement si 'system' est effectivement requis
    # pour ce domaine (inutile, par ex., pour un référentiel RH).
    effective_required = required_fields if required_fields is not None else REQUIRED_FIELDS
    if "system" not in df.columns and "system" in effective_required:
        resolved_system = default_system or path.stem
        df["system"] = resolved_system
        logger.info(f"Colonne 'system' absente du fichier : valeur par défaut appliquée ('{resolved_system}').")

    validate_required_fields(df, required_fields)

    logger.info(f"Ingestion réussie : {len(df)} lignes, colonnes finales : {list(df.columns)}")
    return df


def _read_zip(
    path: Path, column_mapping: dict = None, required_fields: list = None,
    default_system: str | None = None,
) -> pd.DataFrame:
    """
    Extrait une archive ZIP et traite chaque fichier supporté qu'elle
    contient. Deux cas de figure, comme pour un classeur Excel multi-
    feuilles ou un Word multi-tableaux :

    1. Chaque fichier décrit des comptes DIFFÉRENTS (ex. un système par
       fichier) -> les fichiers sont empilés, le nom de chaque fichier
       servant de valeur par défaut pour 'system'.
    2. Les fichiers décrivent les MÊMES comptes avec des colonnes
       différentes (ex. un fichier "identites.csv", un fichier
       "roles.csv" séparé) -> ils sont fusionnés par colonne (jointure
       sur 'username') pour obtenir un enregistrement complet par compte.

    Les fichiers dans un format non supporté ou illisibles sont ignorés
    avec un avertissement, plutôt que de faire échouer tout le traitement.
    """
    import zipfile
    import tempfile

    named_dfs: dict = {}
    skipped = []

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_dir_path = Path(tmp_dir)
        with zipfile.ZipFile(path) as zf:
            zf.extractall(tmp_dir_path)

        candidate_files = sorted(
            p for p in tmp_dir_path.rglob("*")
            if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS
            and not p.name.startswith(".") and "__MACOSX" not in str(p)
        )

        if not candidate_files:
            raise IngestionError(
                f"Aucun fichier de format supporté trouvé dans l'archive {path.name}."
            )

        for f in candidate_files:
            try:
                df = _load_single_file(f, column_mapping, required_fields, _defer_finalize=True)
                named_dfs[f.stem] = df
            except IngestionError as e:
                skipped.append((f.name, str(e)))
                logger.warning(f"Fichier ignoré dans l'archive ({f.name}) : {e}")

    if not named_dfs:
        raise IngestionError(
            f"Aucun fichier exploitable dans l'archive {path.name}. "
            f"Fichiers trouvés mais ignorés : {[s[0] for s in skipped]}"
        )

    logger.info(f"{len(named_dfs)} fichier(s) traité(s) avec succès dans l'archive (sur {len(candidate_files)}).")
    combined = _merge_or_stack_named_tables(named_dfs, default_system, allow_name_as_system=True)

    effective_required = required_fields if required_fields is not None else REQUIRED_FIELDS
    if "system" not in combined.columns and "system" in effective_required:
        resolved_system = default_system or path.stem
        combined["system"] = resolved_system
    validate_required_fields(combined, required_fields)
    return combined


def load_file(path: str | Path, default_system: str | None = None) -> pd.DataFrame:
    """
    Point d'entrée standard : charge un fichier d'export IAM/accès, avec
    le référentiel de colonnes par défaut (config/column_mapping.py).

    `default_system` : nom de système à appliquer si le fichier ne contient
    aucune colonne l'identifiant lui-même (cas fréquent d'un export brut
    d'un seul système). Sans valeur fournie, le nom du fichier sert de
    repli automatique — la fonction ne lève jamais d'erreur pour ce seul
    motif.
    """
    path = Path(path)
    if not path.exists():
        raise IngestionError(f"Fichier introuvable : {path}")

    if path.suffix.lower() == ".zip":
        logger.info(f"Lecture de l'archive : {path.name}")
        return _read_zip(path, default_system=default_system)

    return _load_single_file(path, default_system=default_system)


def load_file_with_mapping(
    path: str | Path, column_mapping: dict, required_fields: list,
    default_system: str | None = None,
) -> pd.DataFrame:
    """
    Variante de load_file() pour un domaine différent de celui des exports
    d'accès (ex. un export RH), avec son propre référentiel de colonnes et
    ses propres champs obligatoires. Réutilise exactement la même logique
    de lecture universelle (tous formats, détection d'en-tête, etc.).
    """
    path = Path(path)
    if not path.exists():
        raise IngestionError(f"Fichier introuvable : {path}")

    if path.suffix.lower() == ".zip":
        logger.info(f"Lecture de l'archive : {path.name}")
        return _read_zip(path, column_mapping, required_fields, default_system=default_system)

    return _load_single_file(path, column_mapping, required_fields, default_system=default_system)


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage : python ingest.py <chemin_fichier>")
        sys.exit(1)
    print(load_file(sys.argv[1]).head())
