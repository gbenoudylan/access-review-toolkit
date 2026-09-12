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
    """
    text = unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode("ascii")
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


if __name__ == "__main__":
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
