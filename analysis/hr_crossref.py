"""
Module de croisement IAM + RH.

Corrige une limite structurelle identifiée sur les exports LDAP/AD purs :
un annuaire ne contient jamais le statut RH réel d'un employé (parti ou
non), puisque cette information vit dans le SIRH, pas dans l'annuaire.

Ce module prend un export IAM (comptes/accès) et un export RH (source de
vérité sur qui est actuellement employé), et enrichit le premier avec le
statut RH réel du second, avant de lancer l'analyse habituelle.

Sans ce croisement, la détection "employé parti mais compte actif" est
strictement impossible sur un export IAM qui ne contient pas nativement
le statut RH (cas de LDIF/LDAP, par exemple).

Rapprochement à deux niveaux, par ordre de préférence :
1. Par identifiant (hr_username) quand le SIRH en fournit un — le plus
   fiable, aucune ambiguïté possible entre deux personnes homonymes.
2. Par nom complet (full_name) en repli, quand le SIRH ne fournit QUE
   des noms (cas réel très courant : la RH suit les personnes par nom,
   pas par identifiant technique) — moins fiable (deux employés peuvent
   partager le même nom), donc les collisions de noms au sein du
   référentiel RH sont explicitement détectées et signalées comme
   ambiguës plutôt que résolues au hasard.
"""

from __future__ import annotations
import logging
import re
import unicodedata

import pandas as pd

from analysis.access_review import _is_active_account

logger = logging.getLogger("hr_crossref")

# Colonnes attendues côté RH — un référentiel de mapping dédié, plus léger
# que celui des accès puisque le besoin est plus restreint.
HR_COLUMN_MAPPING = {
    "hr_username": [
        "username", "user_id", "login", "identifiant", "matricule",
        "employee_id", "sam_account_name", "employee_number",
    ],
    "hr_employee_status": [
        "employee_status", "statut_employe", "hr_status", "statut_rh",
        "employment_status", "statut",
    ],
    "hr_department": [
        "department", "departement", "département", "service",
    ],
    # Nom complet reconstitué automatiquement à partir de first_name +
    # last_name par _synthesize_full_name (ingestion/ingest.py), qui
    # tourne pour N'IMPORTE QUEL mapping de colonnes, pas seulement celui
    # de l'ingestion IAM principale — réutilisé ici sans dupliquer cette
    # logique. Variante "nom" ajoutée en plus des variantes déjà connues
    # de last_name, car un export RH français type "Nom / Prénom" utilise
    # très souvent "Nom" seul (sans "de famille").
    "first_name": ["first_name", "prenom", "prénom", "first name", "given name"],
    "last_name": ["last_name", "nom_famille", "last name", "surname", "family name", "nom"],
    "full_name": [
        "full_name", "nom_complet", "nom complet", "display_name",
        "display name", "fullname", "employee_name", "nom_prenom",
    ],
}

# Volontairement vide : le champ minimal exploitable (identifiant OU nom
# complet) est vérifié explicitement dans cross_reference_with_hr, pas ici
# — un simple champ manquant dans une liste plate ne peut exprimer "l'un
# OU l'autre", contrairement à un contrôle dédié avec un message d'erreur
# qui explique clairement l'alternative possible.
HR_REQUIRED_FIELDS = []


def _normalize_name_bag(name) -> tuple:
    """
    Normalise un nom complet en un multi-ensemble de mots comparables,
    indépendamment de l'ORDRE (Prénom Nom vs Nom Prénom vs 'Nom, Prénom')
    — la seule convention fiable commune à IAM et RH est l'ensemble des
    mots qui composent le nom, pas leur ordre, que rien ne garantit
    identique entre deux systèmes distincts. Accents/casse/espaces/
    ponctuation retirés pour la même raison que la détection de doublons
    (analysis/access_review.py) : deux systèmes différents écrivent
    rarement les noms de façon strictement identique.

    Apostrophe traitée différemment du tiret : une apostrophe précédée
    d'une seule lettre ('N'', 'D'', 'L'', 'O'' — très courant dans les
    patronymes ivoiriens/ouest-africains comme "N'Guessan", "N'Diaye")
    marque presque toujours une contraction, pas une séparation entre
    deux mots distincts — si un système la supprime purement et
    simplement à la saisie ("NGuessan" au lieu de "N'Guessan"), les deux
    doivent rester reconnus comme le même nom. Le tiret, lui, reste un
    séparateur (ex. 'Jean-Pierre' vs 'Jean Pierre' doivent aussi
    correspondre, mais en deux mots distincts) : les deux conventions
    coexistent réellement selon le caractère utilisé.
    """
    text = unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"\b([a-zA-Z])['’]", r"\1", text)
    words = re.findall(r"[a-z]+", text.lower())
    return tuple(sorted(words))




def cross_reference_with_hr(iam_df: pd.DataFrame, hr_df_raw_path: str = None, hr_df: pd.DataFrame = None) -> pd.DataFrame:
    """
    Enrichit iam_df avec le statut RH réel provenant d'un export RH, en les
    rapprochant par nom d'utilisateur (colonne 'username' côté IAM).

    Accepte soit un chemin de fichier RH (hr_df_raw_path, lu et standardisé
    via le moteur d'ingestion générique — tous formats supportés), soit un
    DataFrame RH déjà standardisé (hr_df, pour un usage programmatique).

    Si un compte IAM n'a pas de correspondance dans le référentiel RH, son
    statut est marqué 'Inconnu (absent du référentiel RH)' plutôt que
    d'être silencieusement ignoré — c'est en soi une anomalie à vérifier
    (compte IAM sans employé RH correspondant = potentiel compte fantôme
    ou prestataire externe non déclaré).

    Le statut RH réel (hr_employee_status) écrase 'employee_status' si ce
    dernier était déjà présent côté IAM — la source RH fait autorité.
    """
    if "username" not in iam_df.columns:
        raise ValueError("Le DataFrame IAM doit contenir une colonne 'username'.")

    if hr_df is None:
        if hr_df_raw_path is None:
            raise ValueError("Fournir soit hr_df_raw_path, soit hr_df.")
        from ingestion.ingest import load_file_with_mapping
        hr_df = load_file_with_mapping(hr_df_raw_path, HR_COLUMN_MAPPING, HR_REQUIRED_FIELDS)

    has_hr_username = "hr_username" in hr_df.columns and hr_df["hr_username"].notna().any()
    has_hr_name = "full_name" in hr_df.columns and hr_df["full_name"].notna().any()

    if not has_hr_username and not has_hr_name:
        raise ValueError(
            "Le fichier RH ne fournit ni identifiant exploitable (username, matricule...) "
            "ni nom complet (colonnes Nom/Prénom ou équivalent) — au moins l'un des deux "
            "est nécessaire pour rapprocher les comptes IAM des employés RH."
        )

    iam_df = iam_df.copy()

    if has_hr_username:
        # Rapprochement par IDENTIFIANT — le plus fiable, aucune ambiguïté
        # possible entre deux personnes homonymes.
        hr_lookup = hr_df.drop_duplicates(subset="hr_username").set_index("hr_username")
        # Normalisation (espaces/casse) de la clé de rapprochement : IAM et RH
        # sont deux systèmes distincts, maintenus par des équipes différentes,
        # avec des conventions de casse potentiellement différentes ('jdupont'
        # côté annuaire, 'JDupont' côté SIRH) — sans cette normalisation, une
        # personne réellement employée et présente dans les deux systèmes
        # serait marquée à tort "absente du référentiel RH", un faux positif
        # sérieux pour un contrôle de sécurité (pourrait laisser croire à un
        # compte externe/non déclaré alors que la personne est bien connue).
        hr_lookup.index = hr_lookup.index.astype(str).str.strip().str.lower()
        hr_lookup = hr_lookup[~hr_lookup.index.duplicated(keep="first")]
        match_key = iam_df["username"].astype(str).str.strip().str.lower()
        match_label = "identifiant (username)"
    else:
        # Repli par NOM COMPLET — cas réel fréquent où le SIRH ne suit les
        # employés que par nom, sans identifiant technique partagé avec
        # l'IAM. Moins fiable qu'un identifiant (deux employés peuvent
        # porter le même nom) : les collisions de noms au sein du
        # référentiel RH sont détectées et marquées explicitement ambiguës
        # plutôt que résolues au hasard en gardant la première occurrence.
        if "full_name" not in iam_df.columns:
            raise ValueError(
                "Rapprochement par nom demandé (le fichier RH ne fournit pas d'identifiant "
                "exploitable) mais le DataFrame IAM ne contient pas de colonne 'full_name' "
                "à comparer."
            )
        logger.warning(
            "Aucun identifiant exploitable dans le fichier RH : rapprochement par NOM "
            "COMPLET en repli — moins fiable qu'un identifiant (deux employés peuvent "
            "porter le même nom), résultat à vérifier plus attentivement que d'habitude."
        )
        hr_df = hr_df.copy()
        # Dédoublonnage AVANT de détecter les homonymes : la même
        # personne listée deux fois avec des informations IDENTIQUES
        # (erreur de saisie/copier-coller, plausible dans un référentiel
        # RH maintenu à la main) n'est PAS un homonyme — seules des
        # lignes qui partagent le même nom mais diffèrent sur le reste
        # (statut, département...) représentent une vraie ambiguïté
        # entre deux personnes distinctes.
        dedup_cols = [c for c in hr_df.columns if c != "_name_key"]
        hr_df = hr_df.drop_duplicates(subset=dedup_cols).reset_index(drop=True)
        hr_df["_name_key"] = hr_df["full_name"].apply(_normalize_name_bag)
        name_counts = hr_df["_name_key"].value_counts()
        ambiguous_keys = set(name_counts[name_counts > 1].index)
        if ambiguous_keys:
            logger.warning(
                f"{len(ambiguous_keys)} nom(s) partagé(s) par plusieurs employés distincts "
                "dans le référentiel RH — les comptes IAM correspondants sont marqués "
                "'Ambigu' plutôt que rapprochés au hasard d'un des homonymes."
            )
        hr_lookup = hr_df[~hr_df["_name_key"].isin(ambiguous_keys)].drop_duplicates(
            subset="_name_key"
        ).set_index("_name_key")
        match_key = iam_df["full_name"].apply(_normalize_name_bag)
        ambiguous_mask = match_key.isin(ambiguous_keys)
        match_label = "nom complet"

    matched_status = match_key.map(
        hr_lookup["hr_employee_status"] if "hr_employee_status" in hr_lookup.columns else {}
    ).astype(object)
    if not has_hr_username:
        matched_status[ambiguous_mask] = "Ambigu (plusieurs employés RH portent ce nom)"

    n_unmatched = matched_status.isna().sum()
    if n_unmatched > 0:
        logger.warning(
            f"{n_unmatched} compte(s) IAM sans correspondance dans le "
            f"référentiel RH (rapprochement par {match_label}) — statut marqué comme "
            "inconnu, à vérifier manuellement (compte externe/prestataire non déclaré ?)."
        )

    iam_df["employee_status"] = matched_status.fillna("Inconnu (absent du référentiel RH)")
    iam_df["hr_cross_referenced"] = True

    if "hr_department" in hr_lookup.columns:
        iam_df["department"] = match_key.map(hr_lookup["hr_department"]).fillna(
            iam_df.get("department")
        )

    logger.info(
        f"Croisement RH terminé (par {match_label}) : {len(iam_df) - n_unmatched}/{len(iam_df)} "
        "comptes rapprochés avec succès."
    )

    return iam_df


def _find_column(columns, candidates: list[str]) -> str | None:
    """Trouve la première colonne dont le nom normalisé (espaces/casse/
    accents) correspond à l'un des candidats — tolérant aux variations
    d'écriture réelles ('Nom & Prénoms' / 'Nom et Prénoms' / 'Noms &
    Prénoms'...)."""
    def _norm(s):
        s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode("ascii")
        return re.sub(r"[^a-z]+", " ", s.lower()).strip()

    normalized_candidates = [_norm(c) for c in candidates]
    for col in columns:
        if _norm(col) in normalized_candidates:
            return col
    return None


# Colonnes vues en pratique sur la feuille "Affectation/Mutation" d'un
# fichier RH de mouvements de personnel (départs, promotions, embauches,
# mutations — un classeur, plusieurs feuilles, chacune pour un type de
# mouvement) : la RH y suit les personnes par NOM, jamais par identifiant
# technique partagé avec l'IAM.
_TRANSFER_NAME_COLUMNS = [
    "nom & prénoms", "nom et prénoms", "noms & prénoms", "nom prénoms",
    "nom & prenoms", "nom et prenoms", "nom complet", "full name",
]
_TRANSFER_OLD_DEPT_COLUMNS = ["ancienne direction", "ancien departement", "old department", "ancien service"]
_TRANSFER_NEW_DEPT_COLUMNS = ["nouvelle direction", "nouveau departement", "new department", "nouveau service"]
_TRANSFER_SHEET_NAME_HINTS = ["affectation", "mutation", "transfert", "transfer"]


def load_transferred_employees(file_path, sheet_name: str | None = None) -> pd.DataFrame:
    """
    Charge la liste des employés transférés/mutés depuis un fichier RH de
    mouvements de personnel — typiquement un classeur à plusieurs
    feuilles (une par type de mouvement : départs, embauches,
    promotions, mutations), dont seule la feuille de mutation nous
    intéresse ici. La RH n'y fournit que des noms, jamais d'identifiant
    technique partagé avec l'IAM — voir flag_transferred_but_still_active
    pour le rapprochement par nom qui en découle.

    `sheet_name` : nom exact de la feuille à utiliser. Si omis, la
    première feuille dont le nom contient 'affectation'/'mutation'/
    'transfert' est utilisée — évite d'exiger que l'appelant connaisse
    par avance l'intitulé exact (qui varie d'une entreprise à l'autre,
    ex. 'Affectation/Mutation 2026').

    Retourne un DataFrame avec les colonnes 'full_name' (toujours),
    'old_department' et 'new_department' (si les colonnes correspondantes
    ont été trouvées dans la feuille).
    """
    import openpyxl

    wb = openpyxl.load_workbook(file_path, data_only=True, read_only=True)
    if sheet_name is None:
        matches = [
            s for s in wb.sheetnames
            if any(hint in s.lower() for hint in _TRANSFER_SHEET_NAME_HINTS)
        ]
        if not matches:
            raise ValueError(
                f"Aucune feuille de mutation/affectation trouvée automatiquement parmi "
                f"{wb.sheetnames} — précise le nom exact de la feuille avec sheet_name."
            )
        if len(matches) > 1:
            # Plusieurs feuilles correspondent (ex. classeur archivant
            # plusieurs années : 'Affectation 2025' ET 'Affectation-
            # Mutation 2026') — prendre silencieusement la première
            # reviendrait à risquer d'utiliser une feuille obsolète d'une
            # année précédente sans que personne ne s'en aperçoive.
            # Repli raisonnable : privilégier l'année la plus récente
            # trouvée dans le nom de chaque candidate, puisque ce
            # classeur suit visiblement une convention d'archivage par
            # année. En cas d'égalité (même année ou aucune année
            # détectée dans aucun des noms), refuser de deviner.
            years = {s: re.search(r"(20\d{2})", s) for s in matches}
            years = {s: int(m.group(1)) for s, m in years.items() if m}
            if len(years) == len(matches) and len(set(years.values())) == len(years):
                sheet_name = max(years, key=years.get)
                logger.warning(
                    f"Plusieurs feuilles correspondent à 'mutation/affectation' parmi "
                    f"{matches} — la plus récente par année détectée dans le nom "
                    f"('{sheet_name}') a été retenue. Précise sheet_name explicitement "
                    f"si ce n'est pas la bonne."
                )
            else:
                raise ValueError(
                    f"Plusieurs feuilles correspondent à 'mutation/affectation' sans "
                    f"année exploitable pour trancher sans ambiguïté : {matches} — "
                    f"précise le nom exact de la feuille voulue avec sheet_name."
                )
        else:
            sheet_name = matches[0]
    elif sheet_name not in wb.sheetnames:
        raise ValueError(f"Feuille '{sheet_name}' introuvable. Feuilles disponibles : {wb.sheetnames}")

    ws = wb[sheet_name]
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        return pd.DataFrame(columns=["full_name"])

    # Ligne d'en-tête : la première ligne non vide, comme pour l'ingestion
    # générale — un fichier RH réel commence rarement directement par les
    # données, sans qu'on puisse pour autant supposer que c'est toujours
    # la toute première ligne du classeur.
    header_row_idx = next((i for i, r in enumerate(rows) if any(c is not None for c in r)), None)
    if header_row_idx is None:
        return pd.DataFrame(columns=["full_name"])
    headers = [str(c).strip() if c is not None else "" for c in rows[header_row_idx]]
    data_rows = rows[header_row_idx + 1:]
    raw_df = pd.DataFrame(data_rows, columns=headers)

    name_col = _find_column(raw_df.columns, _TRANSFER_NAME_COLUMNS)
    if name_col is None:
        raise ValueError(
            f"Aucune colonne de nom reconnue sur la feuille '{sheet_name}' parmi "
            f"{list(raw_df.columns)} — attendu une colonne type 'Nom & Prénoms'."
        )

    result = pd.DataFrame({"full_name": raw_df[name_col].astype(str).str.strip()})
    result = result[result["full_name"].str.len() > 0]

    old_col = _find_column(raw_df.columns, _TRANSFER_OLD_DEPT_COLUMNS)
    new_col = _find_column(raw_df.columns, _TRANSFER_NEW_DEPT_COLUMNS)
    if old_col:
        result["old_department"] = raw_df.loc[result.index, old_col]
    if new_col:
        result["new_department"] = raw_df.loc[result.index, new_col]

    return result.reset_index(drop=True)


def flag_transferred_but_still_active(iam_df: pd.DataFrame, transferred_df: pd.DataFrame) -> pd.DataFrame:
    """
    Rapproche par NOM (même logique que le repli de cross_reference_with_hr
    — sac de mots normalisé, indépendant de l'ordre 'Prénom Nom' vs 'Nom
    Prénom') la liste des employés transférés/mutés avec les comptes IAM,
    et ajoute 'is_transferred_but_active' : True pour un compte dont le
    titulaire a été transféré ET dont le compte est encore actif —
    exactement l'anomalie que le contrôle 18 (Terminated Users and
    Transferred users) doit faire ressortir pour la partie "transferred".

    Un homonyme entre deux employés distincts n'est PAS résolu au hasard :
    voir la même logique de détection d'ambiguïté que
    cross_reference_with_hr, avec le même principe (mieux vaut un faux
    positif signalé "à vérifier" qu'un vrai cas raté silencieusement).
    """
    iam_df = iam_df.copy()
    if "full_name" not in iam_df.columns or transferred_df.empty:
        iam_df["is_transferred_but_active"] = False
        return iam_df

    transferred_df = transferred_df.copy()
    # Dédoublonnage AVANT de détecter les homonymes : une même personne
    # listée deux fois avec des informations identiques (erreur de
    # saisie/copier-coller, plausible dans un tableau RH maintenu à la
    # main) n'est PAS un homonyme — seules des lignes qui partagent le
    # même nom mais diffèrent sur le reste (département, date...)
    # représentent une vraie ambiguïté entre deux personnes distinctes.
    original_cols = [c for c in transferred_df.columns if c != "_name_key"]
    transferred_df = transferred_df.drop_duplicates(subset=original_cols).reset_index(drop=True)
    transferred_df["_name_key"] = transferred_df["full_name"].apply(_normalize_name_bag)
    name_counts = transferred_df["_name_key"].value_counts()
    ambiguous_keys = set(name_counts[name_counts > 1].index)
    if ambiguous_keys:
        logger.warning(
            f"{len(ambiguous_keys)} nom(s) partagé(s) par plusieurs personnes transférées "
            "dans le fichier RH — les comptes IAM correspondants sont signalés comme "
            "'transféré (nom ambigu)' plutôt que rapprochés au hasard."
        )
    transferred_keys = set(transferred_df["_name_key"]) - ambiguous_keys

    iam_name_keys = iam_df["full_name"].apply(_normalize_name_bag)
    is_match = iam_name_keys.isin(transferred_keys)
    is_ambiguous_match = iam_name_keys.isin(ambiguous_keys)
    is_active = (
        iam_df["account_status"].apply(_is_active_account)
        if "account_status" in iam_df.columns
        else pd.Series(True, index=iam_df.index)  # statut inconnu : signalé par prudence
    )

    iam_df["is_transferred_but_active"] = (is_match | is_ambiguous_match) & is_active
    iam_df["transferred_name_ambiguous"] = is_ambiguous_match & is_active

    n_flagged = int(iam_df["is_transferred_but_active"].sum())
    if n_flagged:
        logger.info(
            f"{n_flagged} compte(s) actif(s) appartenant à une personne transférée/mutée "
            "identifiée par nom dans le fichier RH — accès à revoir suite à la mutation."
        )
    return iam_df


    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent.parent))

    logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")

    from ingestion.ingest import load_file
    from analysis.access_review import analyze_access, summarize

    if len(sys.argv) < 3:
        print("Usage : python -m analysis.hr_crossref <export_iam> <export_rh>")
        sys.exit(1)

    iam_data = load_file(sys.argv[1])

    enriched = cross_reference_with_hr(iam_data, hr_df_raw_path=sys.argv[2])
    result = analyze_access(enriched)
    print(summarize(result))
