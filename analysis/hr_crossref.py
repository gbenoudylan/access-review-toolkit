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


def _normalize_for_name(s: str) -> str:
    """Normalisation : accents, casse, apostrophe → rien, tiret → espace."""
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode("ascii")
    s = re.sub(r"['\u2019]", "", s)
    s = re.sub(r"[-]", " ", s)
    return s.lower().strip()


def _tokenize_name(s: str) -> list:
    """Découpe un nom en tokens, gère les initiales : 'J.' → ['j']."""
    s = _normalize_for_name(s)
    tokens = []
    for part in s.split():
        part = part.rstrip(".")
        if part:
            tokens.append(part)
    return [t for t in tokens if t]


def _username_derived_from_name(username: str, full_name: str) -> bool:
    """
    Détecte si un username est construit à partir d'un nom complet.

    Pour "Jean Dupont" reconnait :
      jdupont     → initial_prénom + nom
      dupontj     → nom + initial_prénom
      jeand       → prénom + initial_nom
      djean       → initial_nom + prénom
      jd          → initiales combinées
      jean.dupont → prénom.nom (séparé par point ou tiret bas)
      blekpyee    → b + lekpyee (Bobby Lekpyee)

    Longueur minimale 3 chars pour éviter les faux positifs sur les
    tokens trop courts (initiales seules, prénoms de 2 lettres...).
    """
    import itertools
    uname = _normalize_for_name(username)
    # Supprimer suffixes courants de comptes de service
    for suf in ("_sa", "_svc", "_adm", "_admin", ".sa", ".adm", "_ext", "_tmp", "2"):
        if uname.endswith(suf):
            uname = uname[:-len(suf)]; break
    uname_flat = re.sub(r"[._\-]", "", uname)

    tokens = _tokenize_name(full_name)
    if not tokens or not uname_flat:
        return False

    # Séparateurs dans le username → tokeniser et comparer
    if re.search(r"[._]", uname):
        uname_toks = [t for t in re.split(r"[._\-]", uname) if t]
        if len(uname_toks) >= 2 and sorted(uname_toks) == sorted(tokens):
            return True

    # Toutes les permutations de tokens
    for perm in itertools.permutations(tokens):
        # Concaténation complète : jeandupont
        if uname_flat == "".join(perm):
            return True
        # Initial_court + long : jdupont, blekpyee
        for i, long_tok in enumerate(perm):
            for j, short_tok in enumerate(perm):
                if i == j: continue
                if len(long_tok) < 2: continue
                # initial(short) + long
                if uname_flat == short_tok[0] + long_tok and len(long_tok) >= 3:
                    return True
                # long + initial(short)
                if uname_flat == long_tok + short_tok[0] and len(long_tok) >= 3:
                    return True
        # Initiales : jd
        initials = "".join(t[0] for t in perm)
        if len(initials) >= 2 and uname_flat == initials:
            return True
        # Nom seul ou prénom seul (longueur min 4 pour éviter faux positifs)
        # ex. "PRINCE" → match "prince" dans "PRINCE W. GBEADUH"
        # ex. "AZAIOURIS" → match "azaiouris" dans "AZAIOURIS Y. ZEON"
        for tok in perm:
            if len(tok) >= 4 and uname_flat == tok:
                return True

    return False


def _names_match(name_a: str, name_b: str) -> bool:
    """
    Détermine si deux noms/identifiants désignent la même personne.

    Stratégie :
    1. Comparaison de noms avec support des initiales
       'J. Dupont' ↔ 'Jean Dupont', 'HARMONY Z. JAYNES' ↔ 'Harmony Jaynes'
       EXCEPTION : 1 token vs 3+ tokens → les initiales intermédiaires
       (ex. "B" dans "JUAH B C. DIXON") feraient matcher BBA, BCL, BATMAN...
       → aller directement en stratégie 2.
    2. Détection de patterns username depuis un nom complet
       'jdupont' ↔ 'Jean Dupont', 'blekpyee' ↔ 'Bobby Lekpyee'
    """
    toks_a = _tokenize_name(name_a)
    toks_b = _tokenize_name(name_b)
    if not toks_a or not toks_b:
        return False

    short = toks_a if len(toks_a) <= len(toks_b) else toks_b
    long_ = toks_b if len(toks_a) <= len(toks_b) else toks_a

    def _try_match(sh, lo):
        used = set()
        for tok in sh:
            found = False
            for i, t in enumerate(lo):
                if i in used: continue
                if tok == t:
                    used.add(i); found = True; break
                if len(tok) == 1 and t.startswith(tok):
                    used.add(i); found = True; break
                if len(t) == 1 and tok.startswith(t):
                    used.add(i); found = True; break
            if not found:
                return False
        return True

    # Stratégie 1 : comparaison de noms classique
    # Skippée si 1 token vs 3+ tokens : initiales intermédiaires = faux positifs
    if not (len(short) == 1 and len(long_) >= 3):
        if _try_match(short, long_):
            return True

    # Stratégie 2 : pattern username construit depuis un nom
    if len(toks_a) == 1 and len(toks_b) >= 2:
        return _username_derived_from_name(name_a, name_b)
    if len(toks_b) == 1 and len(toks_a) >= 2:
        return _username_derived_from_name(name_b, name_a)

    return False

def _normalize_name_bag(name) -> tuple:
    """Conservé pour compatibilité avec cross_reference_with_hr."""
    text = unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"\b([a-zA-Z])[\'\u2019]", r"\1", text)
    words = re.findall(r"[a-z]+", text.lower())
    return tuple(sorted(words))



def cross_reference_with_hr(
    iam_df: pd.DataFrame, hr_df_raw_path: str = None, hr_df: pd.DataFrame = None,
    custom_mappings: dict = None,
) -> pd.DataFrame:
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
        hr_df = load_file_with_mapping(hr_df_raw_path, HR_COLUMN_MAPPING, HR_REQUIRED_FIELDS, custom_mappings=custom_mappings)

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


def _find_column(
    columns, candidates: list[str], custom_mappings: dict = None, target_field: str = None,
) -> str | None:
    """Trouve la première colonne dont le nom normalisé (espaces/casse/
    accents) correspond à l'un des candidats — tolérant aux variations
    d'écriture réelles ('Nom & Prénoms' / 'Nom et Prénoms' / 'Noms &
    Prénoms'...).

    `custom_mappings`/`target_field` : correspondance apprise
    manuellement (voir ingestion/custom_column_mappings.py), prioritaire
    sur les candidats codés en dur — c'est ce qui permet à cette
    reconnaissance de continuer à s'adapter à de nouveaux intitulés
    jamais vus, sans avoir à modifier le code."""
    def _norm(s):
        s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode("ascii")
        return re.sub(r"[^a-z]+", " ", s.lower()).strip()

    if custom_mappings and target_field:
        for col in columns:
            if custom_mappings.get(_norm(col)) == target_field:
                return col

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
    # Variantes longues
    "nom & prénoms", "nom et prénoms", "noms & prénoms", "nom prénoms",
    "nom & prenoms", "nom et prenoms", "nom complet", "full name",
    "full_name", "fullname", "employee name", "employee_name",
    "nom prénom", "prénom nom", "prenom nom", "nom prenom",
    # Variantes COURTES — colonne "Name" ou "Nom" seuls (cas le plus fréquent)
    "name", "nom", "noms", "names",
    # Variantes francophones/africaines
    "nom & prénom", "agent", "collaborateur", "employe", "employé",
    "personnel", "salarie", "salarié", "identite", "identité",
]
_TRANSFER_OLD_DEPT_COLUMNS = ["ancienne direction", "ancien departement", "old department", "ancien service"]
_TRANSFER_NEW_DEPT_COLUMNS = ["nouvelle direction", "nouveau departement", "new department", "nouveau service"]
_TRANSFER_SHEET_NAME_HINTS = ["affectation", "mutation", "transfert", "transfer"]


class TransferNameColumnNotFoundError(ValueError):
    """Levée par load_transferred_employees quand aucune colonne de nom
    n'a pu être identifiée sur la feuille — porte la liste des colonnes
    brutes (raw_columns) pour que l'appelant (dashboard) puisse proposer
    une correction manuelle plutôt que de simplement échouer."""
    def __init__(self, message: str, raw_columns: list[str]):
        super().__init__(message)
        self.raw_columns = raw_columns


def load_transferred_employees(
    file_path, sheet_name: str | None = None, custom_mappings: dict = None,
) -> pd.DataFrame:
    """
    Charge la liste des employés transférés/mutés.
    Robuste : fonctionne avec n'importe quel fichier Excel/CSV, même si :
    - Il n'a qu'une seule feuille (prise automatiquement)
    - La feuille ne s'appelle pas 'affectation/mutation'
    - Pas de colonnes old/new département
    - Seule une colonne de noms est présente
    """
    import openpyxl

    path = str(file_path)
    suffix = path.rsplit(".", 1)[-1].lower() if "." in path else ""

    if suffix == "csv":
        try:
            raw_df = pd.read_csv(path, sep=None, engine="python", dtype=str)
        except Exception as e:
            raise ValueError(f"Impossible de lire le CSV : {e}")
    else:
        wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
        sheetnames = wb.sheetnames

        if sheet_name and sheet_name in sheetnames:
            target_sheets = [sheet_name]
        elif sheet_name and sheet_name not in sheetnames:
            raise ValueError(f"Feuille '{sheet_name}' introuvable. Disponibles : {sheetnames}")
        else:
            keyword_matches = [
                s for s in sheetnames
                if any(h in s.lower() for h in _TRANSFER_SHEET_NAME_HINTS)
            ]
            if keyword_matches:
                target_sheets = keyword_matches
            else:
                # Feuille unique ou pas de mot-clé → essayer toutes
                target_sheets = sheetnames

        raw_df = None
        used_sheet = None
        for sname in target_sheets:
            ws = wb[sname]
            rows = list(ws.iter_rows(values_only=True))
            if not rows:
                continue
            header_idx = next(
                (i for i, r in enumerate(rows) if any(c is not None for c in r)), None
            )
            if header_idx is None:
                continue
            headers = [str(c).strip() if c is not None else "" for c in rows[header_idx]]
            data_rows = rows[header_idx + 1:]
            candidate = pd.DataFrame(data_rows, columns=headers)
            has_name = _find_column(
                candidate.columns, _TRANSFER_NAME_COLUMNS,
                custom_mappings=custom_mappings, target_field="transfer_full_name"
            ) is not None
            if has_name or sname == target_sheets[-1]:
                raw_df = candidate
                used_sheet = sname
                if has_name:
                    break

        if raw_df is None or raw_df.empty:
            return pd.DataFrame(columns=["full_name"])
        if used_sheet:
            logger.info(f"Feuille '{used_sheet}' utilisée pour les employés transférés.")

    # Colonne de nom explicite
    name_col = _find_column(
        raw_df.columns, _TRANSFER_NAME_COLUMNS,
        custom_mappings=custom_mappings, target_field="transfer_full_name",
    )

    # Repli sur l'intitulé (même heuristique que le fichier Terminated du
    # dashboard) : une colonne 'Nom', 'Name', 'Prénoms'... est prioritaire sur
    # le repli générique, qui pourrait sinon retenir un matricule ou un
    # département à la place des noms.
    if name_col is None:
        for col in raw_df.columns:
            if any(k in str(col).lower() for k in ("name", "nom", "prenom", "prénom", "full")):
                name_col = col
                logger.info(f"Colonne de nom '{col}' détectée par son intitulé.")
                break

    # Fallback générique : première colonne avec des valeurs texte qui ressemblent à des noms
    if name_col is None:
        for col in raw_df.columns:
            if not str(col).strip() or str(col).strip().lower() in ("nan","none",""):
                continue
            sample = raw_df[col].dropna().astype(str).str.strip()
            sample = sample[sample.str.len() > 3]
            if len(sample) > 0 and (sample.str.len() >= 3).mean() > 0.5:
                name_col = col
                logger.info(f"Colonne de nom '{col}' détectée automatiquement.")
                break

    if name_col is None:
        raise TransferNameColumnNotFoundError(
            f"Aucune colonne de nom identifiable parmi {list(raw_df.columns)}. "
            "Renomme la colonne en 'Name', 'Nom', 'Full Name' ou similaire.",
            raw_columns=list(raw_df.columns),
        )

    result = pd.DataFrame({"full_name": raw_df[name_col].astype(str).str.strip()})
    bad = result["full_name"].str.lower().isin(("nan","none","","null","<na>"))
    result = result[~bad & (result["full_name"].str.len() > 0)].copy()

    old_col = _find_column(raw_df.columns, _TRANSFER_OLD_DEPT_COLUMNS,
                           custom_mappings=custom_mappings, target_field="transfer_old_department")
    new_col = _find_column(raw_df.columns, _TRANSFER_NEW_DEPT_COLUMNS,
                           custom_mappings=custom_mappings, target_field="transfer_new_department")
    if old_col:
        result["old_department"] = raw_df.loc[result.index, old_col].values
    if new_col:
        result["new_department"] = raw_df.loc[result.index, new_col].values

    # Garder les autres colonnes pour l'affichage dans le rapport
    for col in raw_df.columns:
        if col and col not in (name_col, old_col, new_col) and col not in result.columns:
            try:
                result[col] = raw_df.loc[result.index, col].values
            except Exception:
                pass

    result = result.reset_index(drop=True)
    # Dédupliquer raw_columns — deux tableaux dans la même feuille
    # peuvent avoir les mêmes noms de colonnes
    seen_cols = set()
    raw_cols_dedup = []
    for col in raw_df.columns:
        c = str(col).strip()
        if c and c.lower() not in ("nan","none","") and c not in seen_cols:
            seen_cols.add(c)
            raw_cols_dedup.append(col)
    result.attrs["raw_columns"] = raw_cols_dedup
    result.attrs["full_column_mapping"] = {
        c: f for c, f in (
            (name_col, "transfer_full_name"),
            (old_col, "transfer_old_department"),
            (new_col, "transfer_new_department"),
        ) if c
    }
    return result


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
    has_iam_name_col = "full_name" in iam_df.columns or "username" in iam_df.columns
    if not has_iam_name_col or transferred_df.empty or "full_name" not in transferred_df.columns:
        iam_df["is_transferred_but_active"] = False
        iam_df["transferred_name_ambiguous"] = False
        return iam_df

    transferred_df = transferred_df.copy()
    original_cols = [c for c in transferred_df.columns if c != "_name_key"]
    transferred_df = transferred_df.drop_duplicates(subset=original_cols).reset_index(drop=True)

    hr_names = transferred_df["full_name"].dropna().astype(str).str.strip().tolist()
    hr_names = [n for n in hr_names if n and n.lower() not in ("nan","none","")]

    if not hr_names:
        iam_df["is_transferred_but_active"] = False
        iam_df["transferred_name_ambiguous"] = False
        return iam_df

    # Détection des homonymes dans la liste RH : si deux personnes distinctes
    # ont le même nom (ou un nom qui matche via initiales), le compte IAM
    # correspondant est signalé "ambigu" plutôt que rattaché au hasard.
    def _count_hr_matches(iam_name: str) -> int:
        if not iam_name or str(iam_name).strip().lower() in ("nan","none",""):
            return 0
        return sum(1 for hr_name in hr_names if _names_match(iam_name, hr_name))

    def _iam_name_matches_any_hr(iam_name: str) -> bool:
        return _count_hr_matches(iam_name) >= 1

    def _iam_name_is_ambiguous(iam_name: str) -> bool:
        return _count_hr_matches(iam_name) > 1

    iam_full_names = iam_df["full_name"].fillna("").astype(str) if "full_name" in iam_df.columns \
                     else pd.Series("", index=iam_df.index)
    iam_usernames  = iam_df["username"].fillna("").astype(str) if "username" in iam_df.columns \
                     else pd.Series("", index=iam_df.index)

    # Matcher contre full_name ET username — même logique que le code terminated.
    # Nécessaire car certains systèmes (Linux, BSS) n'ont pas de colonne Nom :
    # full_name = username dans ce cas. Sans ce fallback, 0 résultats.
    match_by_name = iam_full_names.apply(_iam_name_matches_any_hr)
    match_by_user = iam_usernames.apply(_iam_name_matches_any_hr)
    is_match = match_by_name | match_by_user

    ambig_by_name = iam_full_names.apply(_iam_name_is_ambiguous)
    ambig_by_user = iam_usernames.apply(_iam_name_is_ambiguous)
    is_ambiguous  = ambig_by_name | ambig_by_user

    is_active = (
        iam_df["account_status"].apply(_is_active_account)
        if "account_status" in iam_df.columns
        else pd.Series(True, index=iam_df.index)
    )
    iam_df["is_transferred_but_active"] = is_match & is_active
    iam_df["transferred_name_ambiguous"] = is_ambiguous & is_active

    n_flagged = int(iam_df["is_transferred_but_active"].sum())
    n_ambig   = int(iam_df["transferred_name_ambiguous"].sum())
    if n_ambig:
        logger.warning(
            f"{n_ambig} compte(s) IAM correspondent à plusieurs personnes "
            "transférées différentes (homonymes) — signalés comme ambigus."
        )
    if n_flagged:
        logger.info(
            f"{n_flagged} compte(s) actif(s) appartenant à une personne "
            "transférée/mutée (matching par nom avec initiales)."
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
