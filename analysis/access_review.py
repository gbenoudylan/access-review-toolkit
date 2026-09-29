"""
Module d'analyse de revue des accès (access review).

Détecte les anomalies IAM classiques qu'une revue d'accès périodique doit
identifier :

    1. Comptes d'employés partis, mais encore actifs -> le risque le plus
       critique (accès non révoqué après un départ).
    2. Comptes dormants -> pas de connexion depuis longtemps, candidats à
       la désactivation (principe du least privilege).
    3. Comptes privilégiés dormants -> encore plus critique qu'un compte
       standard dormant, car le niveau d'accès est plus dangereux.
    4. Comptes sans manager/owner identifié -> personne ne peut valider si
       cet accès est toujours légitime lors d'une revue.

Chaque compte reçoit un statut d'action et un niveau de risque, pour
produire une liste priorisée plutôt qu'un simple export brut.
"""

from __future__ import annotations
import logging
import re
import unicodedata
from datetime import datetime

import pandas as pd

logger = logging.getLogger("access_review")
# Clé dans df.attrs indiquant si les données de mot de passe étaient présentes
# dans le fichier source — permet au Control Coverage de distinguer "0 MDP
# périmé" (données présentes, tout va bien) de "N/A" (données absentes, on
# ne sait pas). Sans cette distinction, un fichier sans colonne password_last_set
# afficherait "OK 0" à tort, suggérant que tous les mots de passe sont à jour.
PASSWORD_DATA_PRESENT_ATTR = "_pwd_data_present"

DORMANT_THRESHOLD_DAYS = 90  # seuil standard du secteur (souvent 60-90 jours)
PASSWORD_STALE_THRESHOLD_DAYS = 90  # standard interne MTN : 90 jours pour les comptes standards
RECENTLY_CREATED_THRESHOLD_DAYS = 90  # fenêtre du contrôle 10 "Accounts created"

ACTIVE_STATUS_VALUES = {"active", "actif", "enabled", "activé", "oui", "yes", "y", "true", "1", "open"}

# Valeurs de statut "verrouillé" — traitées comme actives pour l'audit
# (le compte existe, ses rôles sont toujours là, il peut être déverrouillé)
# mais distinctes des vraies valeurs actives : is_locked sera True.
# Intentionnellement séparées d'ACTIVE_STATUS_VALUES et de
# TERMINATED_STATUS_VALUES pour que la logique de résolution puisse
# les reconnaître et les traiter différemment.
LOCKED_STATUS_VALUES = {"locked", "verrouillé", "verrouille"}
TERMINATED_STATUS_VALUES = {
    "terminated", "termine", "terminé", "parti", "departed", "left",
    # "inactive"/"inactif" = disabled
    "inactive", "inactif",
    "resigned", "démissionné",
    "retired", "retraité", "leaver", "ex-employee", "former employee",
    "fired", "dismissed", "licencié", "licencie", "no longer employed",
    "not employed", "separated", "redundant",
    "disabled", "désactivé", "desactive", "desactivé",
    "suspended", "suspendu", "blocked", "bloqué", "expired", "expiré",
    # LOCKED = DISABLED (retour terrain BSS/MTN)
    "locked", "verrouillé", "verrouille",
    # Compte Linux désactivé
    "deactive", "inactive user", "account deactivated",
    # Valeurs booléennes False pour la colonne "Enabled" (PKI, AD exports)
    "false", "0", "no", "non", "n",
}
PRIVILEGED_VALUES = {"oui", "yes", "y", "true", "1", "admin", "administrateur"}
NEVER_EXPIRES_VALUES = {"never expires", "n'expire jamais", "never", "jamais", "no expiry", "does not expire"}

# Format "Generalized Time" utilisé par LDAP/Active Directory pour les dates
# (ex. whenChanged, whenCreated) : YYYYMMDDHHMMSS[.f][Z|+HHMM|-HHMM]
# Supporte maintenant le timezone offset (+0000, -0500, etc.)
_LDAP_GENERALIZED_TIME_RE = re.compile(r"^(\d{14})(\.\d+)?(Z|[+-]\d{4})?$")
# Nombre isolé (sans jour de semaine ni mois) suivi d'une heure, d'un
# fuseau horaire explicite et d'une année — ex. '4 20:09:01 +0000 2025'.
# Rencontré en pratique sur des exports où le jour de semaine ET le mois
# ont été tronqués, ne laissant que le jour du mois. Sans le mois, la
# date réelle est indéterminable ; pandas devinerait sinon ce nombre
# comme un MOIS avec un jour arbitraire (voir _days_since).
_TRUNCATED_DATE_RE = re.compile(r"^\d{1,2}\s+\d{1,2}:\d{2}:\d{2}\s+[+-]\d{4}\s+\d{4}$")
# Date numérique séparée par des virgules (ex. '12,01,2026') : format rare
# comme convention de date légitime — plus vraisemblablement un artefact
# d'un champ CSV mal protégé par des guillemets (une vraie date textuelle
# 'Avril 27, 2022' coupée par erreur sur sa virgule interne, cf. cas réel
# rencontré). Vérifié que pandas ne peut PAS être fait confiance pour ce
# séparateur : il ignore silencieusement dayfirst/yearfirst, peut même
# perdre un groupe entier sans la moindre erreur (ex. '01,25,2026' lu
# comme 1er janvier 2026, le 25 disparaissant purement et simplement) —
# refusé explicitement plutôt que risqué.
_COMMA_SEPARATED_NUMERIC_DATE_RE = re.compile(r"^\d{1,4}\s*,\s*\d{1,4}\s*,\s*\d{1,4}$")

# Mois en français (complets et abrégés, avec ou sans point) -> anglais.
# pandas/dateutil ne reconnaissent que les noms de mois en anglais par
# défaut : un export en français ('Avril 27, 2022', 'Mars 15, 2023')
# échoue silencieusement à l'analyse (None) sans cette traduction — les
# abréviations qui ressemblent par coïncidence à l'anglais ('Sept.',
# 'Oct.') passaient déjà, ce qui masquait le problème pour les autres.
_FRENCH_MONTHS = {
    "janvier": "January", "février": "February", "fevrier": "February", "mars": "March",
    "avril": "April", "mai": "May", "juin": "June", "juillet": "July", "août": "August",
    "aout": "August", "septembre": "September", "octobre": "October", "novembre": "November",
    "décembre": "December", "decembre": "December",
    "janv": "Jan", "févr": "Feb", "fevr": "Feb", "avr": "Apr", "juil": "Jul",
    "sept": "Sep", "oct": "Oct", "nov": "Nov", "déc": "Dec", "dec": "Dec",
}
_FRENCH_MONTH_RE = re.compile(
    r"\b(" + "|".join(sorted(_FRENCH_MONTHS.keys(), key=len, reverse=True)) + r")\.?",
    re.IGNORECASE,
)


def _translate_french_month(text_value: str) -> str:
    return _FRENCH_MONTH_RE.sub(lambda m: _FRENCH_MONTHS[m.group(1).lower()], text_value)


def _tokenize_status_value(value: str) -> set:
    """
    Découpe une valeur de statut en mots-clés individuels sur tout
    séparateur non alphanumérique — 'Y-Active' -> {'y', 'active'},
    'N-inactive' -> {'n', 'inactive'}. Nécessaire pour un vrai format
    d'export rencontré (Oracle EBS, MTN) où le statut combine un code
    lettre et un mot descriptif dans la même valeur ('Y-Active' /
    'N-inactive'), qu'une simple égalité de chaîne entière ne
    reconnaît jamais. ATTENTION au piège : 'N-inactive' contient
    'active' comme SOUS-CHAÎNE ('inactive'.find('active') réussit),
    donc la comparaison doit se faire par MOT ENTIER (jeton), jamais
    par recherche de sous-chaîne, sous peine de classer 'inactive'
    comme actif à tort.
    """
    return set(re.split(r"[^a-z0-9]+", str(value).strip().lower())) - {""}


_NEGATION_TOKENS = {"not", "non", "no", "sans"}


def _is_active_account(value) -> bool:
    if value is None:
        return False
    normalized = str(value).strip().lower()
    if normalized in ACTIVE_STATUS_VALUES:
        return True
    tokens = _tokenize_status_value(value)
    if tokens & _NEGATION_TOKENS:
        # Garde contre un faux positif : 'Not Active' ou 'Non Actif'
        # contient le mot 'active' mais signifie précisément l'inverse —
        # une négation explicite l'emporte toujours sur la présence du
        # mot-clé positif.
        return False
    return bool(tokens & ACTIVE_STATUS_VALUES)


def _is_terminated_employee(value) -> bool:
    if value is None:
        return False
    normalized = str(value).strip().lower()
    if normalized in TERMINATED_STATUS_VALUES:
        return True
    return bool(_tokenize_status_value(value) & TERMINATED_STATUS_VALUES)


def _is_privileged(value) -> bool:
    if value is None:
        return False
    return str(value).strip().lower() in PRIVILEGED_VALUES


def _has_non_expiring_password(value) -> bool:
    if value is None:
        return False
    return str(value).strip().lower() in NEVER_EXPIRES_VALUES


def _is_service_account_name(username) -> bool:
    """
    Convention de nommage standard du secteur pour les comptes de service :
    préfixe ou suffixe 'svc_' (ex. 'svc_ise', 'ise_svc'), insensible à la
    casse. Ne signale rien si la colonne est absente ou vide.
    """
    if username is None:
        return False
    name = str(username).strip().lower()
    return name.startswith("svc_") or name.endswith("_svc") or name.startswith("svc-") or name.endswith("-svc")


# Motifs de nommage courants pour un compte de test/UAT/QA/démo — repère
# un jeton distinct ('test', 'uat', 'qa'...), pas une simple sous-chaîne
# n'importe où (qui attraperait à tort un nom de famille contenant ces
# lettres, ex. 'Testard'). Toujours combiné à une action de VÉRIFICATION,
# jamais une désactivation automatique : la détection par nom seul reste
# un indice, pas une certitude.
_TEST_ACCOUNT_RE = re.compile(
    # 'test' SANS frontière de mot : preuve réelle rencontrée en pratique
    # (comptes 'testadmin', 'dtest', 'sdptester', 'MTNtester' — le mot
    # est presque toujours accolé directement à un autre fragment, pas
    # isolé par un séparateur) — une frontière stricte comme pour les
    # autres mots-clés ratait la quasi-totalité de ces comptes réels.
    # Les autres mots-clés gardent une frontière stricte : plus courts
    # ('qa') ou plus fréquents comme fragment de mot ordinaire ('demo'
    # dans un patronyme comme 'Demontigny', 'qa' dans 'Qatar') — sans
    # preuve équivalente que ces derniers s'accolent aussi en pratique,
    # les assouplir ferait plus de mal (faux positifs sur de vrais noms)
    # que de bien.
    r"test(ing)?|(^|[_\-\.])(uat|qa|dummy|demo|sandbox)(ing)?([_\-\.]|\d|$)",
    re.IGNORECASE,
)

# Comptes génériques (contrôle 3, "Orphaned Accounts") : un compte dont le
# nom ne permet pas d'identifier une personne précise (admin, support,
# service...) est le cas type d'un compte "orphelin" au sens du contrôle
# ("no information that would allow the holder to be positively
# identified"). Liste et méthode (recherche de sous-chaîne, sans
# frontière de mot, insensible à la casse) reprises telles quelles d'une
# formule Excel (ESTNUM(CHERCHE(...))) déjà utilisée en production —
# fidèlement reproduites plutôt que réinterprétées, pour donner
# exactement le même résultat que le processus existant.
_GENERIC_ACCOUNT_MARKERS = [
    "user", "admin", "guest", "support", "service", "root", "info",
    "system", "manager", "backup", "operator", "developer", "superuser",
    "anonymous", "account", "public", "maintenance", "sales",
]
_ORPHANED_ACCOUNT_RE = re.compile(
    "|".join(re.escape(m) for m in _GENERIC_ACCOUNT_MARKERS), re.IGNORECASE
)


def _is_orphaned_account_name(username) -> bool:
    if username is None:
        return False
    return bool(_ORPHANED_ACCOUNT_RE.search(str(username).strip()))


def _is_test_account_name(username) -> bool:
    if username is None:
        return False
    return bool(_TEST_ACCOUNT_RE.search(str(username).strip()))


_AMBIGUOUS_DATE_START_RE = re.compile(r"^(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{2,4})")


def _detect_dayfirst(series: pd.Series) -> tuple[bool, str]:
    """
    Détermine si une colonne de dates au format 'A/B/Année' (non ISO,
    donc potentiellement ambigu) doit être lue jour-premier (JJ/MM,
    standard francophone/africain) ou mois-premier (MM/JJ, standard
    américain — ex. un export venant d'un outil IAM/SIEM américain,
    rencontré en pratique même dans un contexte MTN).

    Retourne (dayfirst, statut) où statut est :
        - "proven"  : convention réellement prouvée par au moins une
          valeur, cohérente sur toute la colonne — fiable.
        - "guessed" : aucune valeur de la colonne ne permet de trancher —
          repli par défaut (jour-premier, standard MTN), à traiter comme
          une supposition, pas une certitude.
        - "mixed"   : la colonne contient des preuves CONTRADICTOIRES —
          au moins une valeur prouve jour-premier ET au moins une autre
          prouve mois-premier. Les exports fusionnant plusieurs systèmes
          sources (chacun avec sa propre convention) peuvent réellement
          mélanger les deux formats au sein d'une même colonne : dans ce
          cas, seules les valeurs qui ont LEUR PROPRE preuve individuelle
          peuvent être déterminées de façon fiable (voir _days_since) —
          les valeurs individuellement ambiguës dans une colonne "mixed"
          sont refusées plutôt que rattachées à une convention majoritaire
          qui pourrait très bien ne pas être la leur.

    Un même fichier utilise presque toujours une convention cohérente sur
    toute sa colonne : on cherche donc, dans les valeurs réellement
    présentes, au moins UNE valeur qui lève l'ambiguïté (un groupe > 12,
    qui ne peut donc pas être un mois) plutôt que de deviner à l'aveugle.
    Toute la colonne est parcourue (pas seulement jusqu'à la première
    preuve trouvée) pour détecter un mélange réel de conventions.
    """
    dayfirst_evidence = 0
    monthfirst_evidence = 0
    for value in series.dropna():
        match = _AMBIGUOUS_DATE_START_RE.match(str(value).strip())
        if not match:
            continue
        first, second = int(match.group(1)), int(match.group(2))
        if first > 12:
            dayfirst_evidence += 1
        elif second > 12:
            monthfirst_evidence += 1

    if dayfirst_evidence and monthfirst_evidence:
        logger.warning(
            f"Convention de date MÉLANGÉE détectée dans une colonne : "
            f"{dayfirst_evidence} valeur(s) prouvent jour-premier (JJ/MM), "
            f"{monthfirst_evidence} valeur(s) prouvent mois-premier (MM/JJ). "
            f"Chaque valeur ayant sa propre preuve individuelle sera lue "
            f"correctement quelle que soit sa convention ; les valeurs "
            f"individuellement ambiguës (ex. '03/04/2026') ne peuvent en "
            f"revanche pas être rattachées de façon fiable à l'une ou "
            f"l'autre convention et resteront non exploitables plutôt que "
            f"devinées au hasard."
        )
        return dayfirst_evidence >= monthfirst_evidence, "mixed"
    if dayfirst_evidence:
        return True, "proven"
    if monthfirst_evidence:
        return False, "proven"
    # Aucune preuve trouvée nulle part dans la colonne : repli par défaut,
    # signalé comme une supposition plutôt que présenté avec la même
    # assurance qu'une convention réellement prouvée.
    logger.warning(
        "Convention jour/mois non déterminable pour une colonne de dates "
        "(aucune valeur ne permet de trancher entre JJ/MM et MM/JJ) — "
        "repli par défaut sur jour-premier (standard MTN), à vérifier "
        "si le fichier provient d'un système utilisant une autre convention."
    )
    return True, "guessed"



def _detect_yearfirst(series: pd.Series) -> tuple[bool, str]:
    """
    Détermine si une colonne 'A-B-C' à année sur 2 chiffres place l'année
    en PREMIER (ex. '26-01-15' pour le 15/01/2026) plutôt qu'en dernier
    (ex. '15-01-26' pour la même date, ordre JJ-MM-AA ou MM-JJ-AA). Le
    groupe du milieu est toujours le mois quelle que soit la convention ;
    seule la position de l'année (1er ou 3e groupe) reste à déterminer.

    Retourne (yearfirst, statut) — même principe que _detect_dayfirst :
        - "proven"  : au moins une valeur prouve sans ambiguïté la
          position de l'année (voir preuves ci-dessous), cohérente sur
          toute la colonne.
        - "guessed" : aucune valeur ne permet de trancher — yearfirst
          n'est PAS forcé (on laisse _detect_dayfirst trancher jour/mois
          comme pour un format classique).
        - "mixed"   : des valeurs de la colonne prouvent des positions
          d'année CONTRADICTOIRES — un export fusionnant plusieurs
          systèmes sources peut réellement mélanger les deux. Toute la
          colonne est parcourue (pas seulement jusqu'à la première
          preuve) pour détecter ce cas plutôt que le masquer derrière la
          première preuve rencontrée.

    Preuves recherchées (un groupe > 31 ne peut être qu'une année, aucun
    jour ne dépassant 31) :
        - 1er groupe > 31 -> preuve directe que l'année est en PREMIER.
        - 3e groupe > 31  -> preuve directe que l'année est en DERNIER
          (symétrique, jamais vérifiée par l'ancienne version de cette
          fonction, qui ne pouvait donc jamais prouver cette direction —
          seulement la déduire par défaut, à tort, avec la même
          assurance qu'une vraie preuve).
    """
    yearfirst_evidence = 0
    yearlast_evidence = 0
    for value in series.dropna():
        match = _AMBIGUOUS_DATE_START_RE.match(str(value).strip())
        if not match:
            continue
        first, third = int(match.group(1)), int(match.group(3))
        if first > 31:
            yearfirst_evidence += 1
        elif len(match.group(3)) == 2 and third > 31:
            yearlast_evidence += 1

    if yearfirst_evidence and yearlast_evidence:
        logger.warning(
            f"Position de l'année MÉLANGÉE détectée dans une colonne à année "
            f"sur 2 chiffres : {yearfirst_evidence} valeur(s) prouvent l'année "
            f"en premier, {yearlast_evidence} valeur(s) prouvent l'année en "
            f"dernier. Chaque valeur ayant sa propre preuve individuelle sera "
            f"lue correctement ; les valeurs individuellement ambiguës ne "
            f"peuvent en revanche pas être rattachées de façon fiable à l'une "
            f"ou l'autre position."
        )
        return yearfirst_evidence >= yearlast_evidence, "mixed"
    if yearfirst_evidence:
        return True, "proven"
    if yearlast_evidence:
        return False, "proven"
    return False, "guessed"


def _combine_convention_status(*statuses: str) -> str:
    """
    Combine les statuts de _detect_dayfirst et _detect_yearfirst pour une
    même colonne — le pire des deux l'emporte : si l'un des deux aspects
    (jour/mois OU position de l'année) est prouvé mélangé, la colonne
    entière doit être traitée avec la prudence "mixed" par _days_since,
    même si l'autre aspect est parfaitement prouvé.
    """
    if "mixed" in statuses:
        return "mixed"
    if "guessed" in statuses:
        return "guessed"
    return "proven"


def _days_since(
    date_value, dayfirst: bool = True, yearfirst: bool = False,
    reference_datetime: datetime | None = None,
    column_convention_status: str = "proven",
) -> float | None:
    """
    Retourne le nombre de jours écoulés depuis une date, ou None si non
    calculable. Gère aussi le format de date LDAP/Active Directory
    (Generalized Time, ex. '20260807120000.0Z'), non reconnu nativement
    par le parseur de dates générique.

    `dayfirst` et `yearfirst` : à déterminer par colonne via
    _detect_dayfirst/_detect_yearfirst plutôt que de supposer une
    convention unique valable pour tous les fichiers — différents
    systèmes sources (ex. un outil IAM américain vs un export AD local)
    peuvent utiliser des conventions différentes au sein d'une même
    entreprise.

    `column_convention_status` : statut retourné par _detect_dayfirst
    ("proven" / "guessed" / "mixed"). Quand la colonne mélange RÉELLEMENT
    deux conventions ("mixed" — plusieurs systèmes sources fusionnés,
    chacun avec son propre format), une valeur qui a SA PROPRE preuve
    individuelle (un groupe > 12) est toujours lue correctement quel que
    soit ce statut ; mais une valeur individuellement ambiguë (les deux
    groupes <= 12, ex. '03/04/2026') ne peut alors être rattachée en
    toute confiance à aucune des deux conventions prouvées dans la
    colonne — refusée explicitement plutôt que devinée via la convention
    majoritaire, qui pourrait très bien ne pas être la sienne.

    `reference_datetime` : date de référence pour le calcul de
    l'ancienneté — datetime.now() par défaut, mais peut être fixée
    explicitement pour qu'une revue reste rejouable à l'identique des
    mois plus tard (reproductibilité d'audit : "pourquoi ce compte
    était-il dormant lors de la revue du 15 juin ?" doit redonner
    exactement le même résultat, pas un résultat qui dérive avec la date
    du jour où on relance l'analyse).
    """
    reference_datetime = reference_datetime or datetime.now()
    if pd.isna(date_value) or date_value is None:
        return None

    text_value = str(date_value).strip()

    # Format date Linux (commande `last`) : "Nov 26 22:23:06 +0100 2025"
    # Mois abrégé + Jour + Heure + Timezone + Année (année en FIN)
    _linux_last_pattern = re.compile(
        r"^([A-Za-z]{3})\s+(\d{1,2})\s+(\d{1,2}:\d{2}:\d{2})\s+([+-]\d{4})\s+(\d{4})$"
    )
    m_linux = _linux_last_pattern.match(text_value.strip())
    if m_linux:
        month, day, time_, tz, year = m_linux.groups()
        try:
            reconstructed = f"{day} {month} {year} {time_} {tz}"
            parsed_dt = pd.to_datetime(reconstructed, format="%d %b %Y %H:%M:%S %z", dayfirst=True)
            if parsed_dt.tzinfo is not None:
                parsed_dt = parsed_dt.tz_convert("UTC").tz_localize(None)
            ref_naive = reference_datetime.replace(tzinfo=None)
            return (ref_naive.date() - parsed_dt.to_pydatetime().date()).days
        except Exception:
            pass

    # Format Oracle nanoseconde : "2/14/2021 8:40:26.000000000 AM +00:00"
    # Les 9 chiffres après la virgule sont des nanosecondes — non supporté
    # nativement par strptime. On les retire avant de parser.
    _nano_pattern = re.compile(
        r"^(\d{1,2}/\d{1,2}/\d{4}\s+\d{1,2}:\d{2}:\d{2})\.\d+(\s*[AP]M\s*.*)$",
        re.IGNORECASE,
    )
    nano_m = _nano_pattern.match(text_value)
    if nano_m:
        text_value = nano_m.group(1) + nano_m.group(2).rstrip()
        # Supprimer le timezone tronqué "+00:" → "+00:00"
        text_value = re.sub(r"\+00:0?$", "+00:00", text_value)
        try:
            parsed_dt = pd.to_datetime(text_value, dayfirst=False)
            if parsed_dt.tzinfo is not None:
                parsed_dt = parsed_dt.tz_convert("UTC").tz_localize(None)
            ref_naive = reference_datetime.replace(tzinfo=None)
            return (ref_naive.date() - parsed_dt.to_pydatetime().date()).days
        except Exception:
            pass

    # Colonne prouvée "mixed" (voir _detect_dayfirst) : une valeur qui n'a
    # PAS sa propre preuve individuelle (ni premier ni second groupe > 12)
    # ne peut être rattachée en confiance à aucune des deux conventions
    # cohabitant dans la colonne — refusée plutôt que devinée au hasard
    # via la convention majoritaire.
    if column_convention_status == "mixed":
        ambiguous_match = _AMBIGUOUS_DATE_START_RE.match(text_value)
        if ambiguous_match:
            first, second = int(ambiguous_match.group(1)), int(ambiguous_match.group(2))
            if first <= 12 and second <= 12:
                return None

    # Motif tronqué rencontré en pratique (export coupant le jour de la
    # semaine ET le mois, ne laissant qu'un nombre isolé en tête — ex.
    # '4 20:09:01 +0000 2025' au lieu de 'Fri Nov 4 20:09:01 +0000 2025').
    # Sans le mois, la date réelle est indéterminable : pandas devine
    # silencieusement ce nombre isolé comme un MOIS avec jour=1 inventé
    # (ex. '4 ...' lu comme 1er avril), ce qui produit une date fausse
    # sans la moindre erreur. On refuse explicitement de deviner ici —
    # mieux vaut aucune date que la mauvaise.
    if _TRUNCATED_DATE_RE.match(text_value):
        return None

    if _COMMA_SEPARATED_NUMERIC_DATE_RE.match(text_value):
        return None

    ldap_match = _LDAP_GENERALIZED_TIME_RE.match(text_value)
    if ldap_match:
        try:
            parsed_dt = datetime.strptime(ldap_match.group(1), "%Y%m%d%H%M%S")
            return (reference_datetime.date() - parsed_dt.date()).days
        except ValueError:
            return None

    # YYYYMMDD sans heure (ex. "20260622") — 8 chiffres seuls
    if re.fullmatch(r"\d{8}", text_value):
        try:
            parsed_dt = datetime.strptime(text_value, "%Y%m%d")
            return (reference_datetime.date() - parsed_dt.date()).days
        except ValueError:
            pass

    # Numéro de série Excel (ex. 45678) : un export Excel dont la colonne
    # a perdu son format "Date" affiche parfois le nombre brut de jours
    # depuis le 30/12/1899. pd.to_datetime() sur un simple entier
    # l'interprète par défaut comme des nanosecondes depuis 1970, ce qui
    # produit une date totalement fausse (des dizaines d'années d'écart)
    # sans la moindre erreur visible. On détecte ce cas précisément :
    # une valeur purement numérique, sans séparateur de date (-, /, :),
    # dans une plage plausible pour un export récent (env. 1990-2100).
    is_bare_number = re.fullmatch(r"\d+(\.\d+)?", text_value) is not None
    if is_bare_number:
        serial = float(text_value)
        if 32874 <= serial <= 73050:  # ~ 01/01/1990 à 01/01/2100
            excel_epoch = datetime(1899, 12, 30)
            parsed_dt = excel_epoch + pd.Timedelta(days=serial)
            return (reference_datetime.date() - parsed_dt.date()).days
        return None  # nombre hors plage plausible : probablement pas une date

    # dayfirst=True lève l'ambiguïté JJ/MM (voir plus haut), mais appliqué
    # à un format déjà commençant par l'année (ISO, ex. '2026-09-01') il
    # produit l'effet inverse : pandas peut alors interpréter le second et
    # troisième groupe comme JOUR-MOIS plutôt que MOIS-JOUR, inversant
    # silencieusement une date par ailleurs déjà non ambiguë (le 1er
    # septembre devient le 9 janvier). On ne force donc dayfirst que
    # lorsque l'année n'est PAS le premier groupe du texte.
    #
    # Dans un format à 3 groupes ('A-B-C'), le groupe du MILIEU est
    # toujours le mois, quelle que soit la convention (AAAA-MM-JJ,
    # JJ-MM-AAAA, MM-JJ-AAAA) — seule l'identité du 1er groupe (jour ou
    # année) reste ambiguë quand l'année est écrite sur 2 chiffres. Un
    # 1er groupe > 31 ne peut alors être qu'une année (aucun jour ne
    # dépasse 31) : ex. '26-01-15' pour 2026-01-15, sans quoi il aurait
    # été lu comme le 26 janvier 2015 (décalage de 11 ans, silencieux).
    year_first_4digit = bool(re.match(r"^\d{4}[-/.]", text_value))
    year_first = year_first_4digit or yearfirst

    # Mois en français ('Avril', 'Mars'...) non reconnus par le parseur
    # par défaut (anglais) : traduits avant analyse. Les abréviations qui
    # ressemblent par coïncidence à l'anglais ('Sept.', 'Oct.') passaient
    # déjà, ce qui masquait le problème pour les mois complets.
    translated_text = _translate_french_month(text_value)
    parse_input = translated_text if translated_text != text_value else date_value

    # Date sans année (ex. 'Fri Jan 17 16:05') : pandas suppose l'année 1.
    has_no_year = not re.search(r"\d{4}", text_value)

    # ── Stratégie 1 : pandas avec la convention détectée ──────────────
    try:
        import warnings
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", message=".*dayfirst.*")
            warnings.filterwarnings("ignore", message=".*nanoseconds.*")
            parsed = pd.to_datetime(
                parse_input, errors="coerce",
                dayfirst=dayfirst and not year_first,
                yearfirst=year_first and not year_first_4digit,
            )
            if not pd.isna(parsed):
                parsed_dt = parsed.to_pydatetime().replace(tzinfo=None)
                if has_no_year and parsed_dt.year < 1900:
                    candidate = parsed_dt.replace(year=reference_datetime.year)
                    if candidate.date() > reference_datetime.date():
                        candidate = candidate.replace(year=candidate.year - 1)
                    parsed_dt = candidate
                return (reference_datetime.date() - parsed_dt.date()).days
    except Exception:
        pass

    # ── Stratégie 2 : formats ISO et courants via liste exhaustive ─────
    _EXPLICIT_FORMATS = [
        # Avec heure + timezone offset
        "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S.%f%z",
        "%Y-%m-%d %H:%M:%S%z", "%Y-%m-%d %H:%M:%S.%f%z",
        "%d/%m/%Y %H:%M:%S%z", "%m/%d/%Y %H:%M:%S%z",
        # Sans timezone
        "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f",
        "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f",
        "%d/%m/%Y %H:%M:%S", "%m/%d/%Y %H:%M:%S",
        "%d-%m-%Y %H:%M:%S", "%d.%m.%Y %H:%M:%S",
        # Date seule
        "%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y",
        "%d-%m-%Y", "%m-%d-%Y",
        "%d.%m.%Y", "%Y.%m.%d",
        "%d %b %Y", "%d %B %Y",
        "%b %d, %Y", "%B %d, %Y",
        "%b %d %Y", "%Y/%m/%d",
        # Avec AM/PM
        "%m/%d/%Y %I:%M:%S %p", "%d/%m/%Y %I:%M:%S %p",
        "%m/%d/%Y %I:%M %p",
    ]
    clean = text_value.rstrip()
    for fmt in _EXPLICIT_FORMATS:
        try:
            parsed_dt = datetime.strptime(clean, fmt)
            if parsed_dt.tzinfo is not None:
                parsed_dt = parsed_dt.replace(tzinfo=None)
            return (reference_datetime.date() - parsed_dt.date()).days
        except (ValueError, OverflowError):
            continue

    # ── Stratégie 3 : dateutil — fallback universel ─────────────────────
    # dateutil.parser.parse reconnaît pratiquement tous les formats texte
    # connus. C'est le filet de sécurité final avant d'abandonner.
    try:
        from dateutil import parser as _dateutil_parser
        parsed_dt = _dateutil_parser.parse(
            translated_text,
            dayfirst=dayfirst,
            yearfirst=year_first,
            fuzzy=False,  # pas de fuzzy : évite de parser du texte non-date
        )
        parsed_dt = parsed_dt.replace(tzinfo=None)
        return (reference_datetime.date() - parsed_dt.date()).days
    except Exception:
        pass

    return None


# Un compte peut être signalé privilégié de deux façons différentes
# selon l'export : une colonne booléenne dédiée ('Sudo Privileges:
# Yes/No'), OU seulement via l'intitulé du rôle lui-même ('Role:
# Administrator') sans colonne booléenne séparée — cas réel rencontré
# sur des exports serveur. Ignorer la seconde ferait passer à travers
# les mailles du filet tous les comptes administrateurs d'un fichier
# qui n'a que ce seul indicateur.
#
# Recherche par MOT ENTIER (limites \b), pas par simple sous-chaîne :
# un simple "in" faisait remonter en masse de faux positifs sur des
# intitulés qui contiennent "admin" sans être un privilège IT réel
# ('Administrative Assistant', 'Sales Administration'...) — repéré
# sur un vrai fichier où ça avait fait exploser le compteur de
# comptes privilégiés à un niveau franchement irréaliste (~60% du
# fichier), signe évident du faux positif plutôt que d'un vrai
# résultat.
# ── Mots-clés standard (frontières de mot anglaises) ──────────────────
# Rôles dont le nom contient ces termes comme mots entiers — capturés
# par la regex \b(keyword)\b. Les tirets et espaces fonctionnent comme
# frontières. ATTENTION : l'underscore _ est un caractère de mot en
# regex Python, donc \b ne sépare PAS "mtn_fa_responsable" en mots
# distincts — les rôles MTN underscore sont gérés séparément ci-dessous.
_STD_PRIVILEGED_KEYWORDS = [
    # Admin générique
    "admin", "administrator", "administrateur", "sysadmin", "sys admin",
    "superadmin", "super admin",
    # Super user (espace entre super et user : frontières correctes)
    "superuser", "super user",
    # Rôles Oracle EBS avec espaces (word boundaries fonctionnent)
    "system administrator", "system administration",
    "application developer",
    "cash management superuser",
    "general ledger superuser", "general ledger super user",
    "payables superuser", "receivables superuser",
    "inventory superuser", "purchasing superuser",
    "order management super user", "order management superuser",
    "hr superuser", "treasury superuser",
    "general ledger euro super user",
    "bis super user", "gl super user",
    "fixed assets administrator", "fixed assets manager",
    "demand planning administrator", "demand planning system administrator",
    "global warehouse administrator",
    "warehouse manager",
    "trading community manager",
    "oracle pricing manager",
    # Superviseur Oracle EBS
    "general ledger budget supervisor",
    "ax general ledger supervisor", "ax payables supervisor", "ax receivables supervisor",
    "ax developer",
    # Accès complets
    "full access", "full control", "all access",
    "privileged user", "power user",
    # Unix/Linux/AD
    "root", "sudoer", "domain admin", "enterprise admin", "schema admin",
    "local admin", "backup operator", "account operator",
    # BD
    "dba", "database admin", "database administrator",
    # Outil tiers à accès direct GL
    "excel4apps",  # Excel4apps Wands = écriture directe dans Oracle GL via Excel
]
_STD_RE = re.compile(
    r"(?<![a-z0-9_])(" + "|".join(re.escape(k) for k in _STD_PRIVILEGED_KEYWORDS) + r")(?![a-z0-9_])",
    re.IGNORECASE,
)

# ── Patterns MTN underscore ────────────────────────────────────────────
# Les codes MTN utilisent _ comme séparateur (mtn_fa_responsable,
# mtn_inv_resp_stock_ventes) : \b ne fonctionne pas car _ est un
# caractère de mot. On utilise une recherche de sous-chaîne directe.
_MTN_PRIVILEGED_SUBSTRINGS = [
    "responsable",    # mtn_ap - responsable, mtn_fa_responsable,
                      # mtn_gl - responsable_*, mtn_inv_responsable_stock_*
    "_resp_",         # mtn_om_resp_pnr, mtn_om_resp_oue, mtn_inv_resp_stock_*
    "gestionnaire",   # mtn_inv_gestionnaire_stock
    "_manager",       # mtn_po_manager (mais PAS "mtn_OM_Vendeur" → bien)
    "accès complet",  # mtn_accès complet à isupplier portal (accès total)
    "_ajustement",    # mtn_inv_ajustement = peut modifier le stock directement
    "_ar - responsable",   # mtn_ar - responsable interface bscs
]

def _is_privileged_role_value(role_str: str) -> bool:
    """
    Détection de privilège depuis le contenu d'un rôle — deux niveaux :
    1. Regex sur mots-clés anglais (word boundaries adaptés).
    2. Sous-chaîne pour les codes MTN underscore où \b ne s'applique pas.
    """
    if not role_str or role_str in ("nan", "none", ""):
        return False
    low = role_str.strip().lower()
    if _STD_RE.search(low):
        return True
    return any(sub in low for sub in _MTN_PRIVILEGED_SUBSTRINGS)

def analyze_access(
    df: pd.DataFrame,
    dormant_threshold_days: int = DORMANT_THRESHOLD_DAYS,
    password_stale_threshold_days: int = PASSWORD_STALE_THRESHOLD_DAYS,
    never_used_threshold_days: int = 30,
    recently_created_threshold_days: int = RECENTLY_CREATED_THRESHOLD_DAYS,
    reference_datetime: datetime | None = None,
    custom_status_mappings: dict | None = None,
    custom_rights_mappings: dict | None = None,
) -> pd.DataFrame:
    """
    Analyse un DataFrame standardisé (sortie du module d'ingestion) et
    ajoute les colonnes de diagnostic :

        - days_since_last_login : ancienneté de connexion (None si non calculable)
        - is_dormant : True si inactif depuis plus que le seuil
        - is_terminated_but_active : True si l'employé est parti mais le compte
          reste actif -> anomalie critique
        - is_privileged_flag : True si le compte a des droits privilégiés
        - has_no_manager : True si aucun manager/owner identifié
        - review_action : action recommandée
        - risk_level : niveau de risque (Critique / Élevé / Moyen / Faible)
    """
    df = df.copy()
    # Garantir un index entier unique et contigu — requis par plusieurs
    # opérations internes (reindex, loc avec masque booléen, transform).
    # Un fichier Excel mal lu ou un concat sans ignore_index peut produire
    # des index dupliqués qui font planter ces opérations silencieusement.
    if not df.index.is_unique or not isinstance(df.index, pd.RangeIndex):
        df = df.reset_index(drop=True)
    reference_datetime = reference_datetime or datetime.now()

    if "last_login_date" in df.columns:
        _truncated_count = df["last_login_date"].astype(str).str.match(_TRUNCATED_DATE_RE).sum()
        if _truncated_count:
            logger.warning(
                f"{_truncated_count} date(s) de dernière connexion au format tronqué "
                f"(jour de semaine et mois manquants, ex. '4 20:09:01 +0000 2025') — "
                f"non exploitables, à corriger à la source plutôt que devinées. "
                f"Ces comptes sont signalés (pire cas, cohérent avec le traitement des "
                f"comptes jamais connectés) plutôt que silencieusement exclus du contrôle "
                f"de dormance — leur ancienneté réelle de connexion reste inconnue, à "
                f"vérifier manuellement plutôt que présumée."
            )
        _dayfirst_login, _login_date_status = _detect_dayfirst(df["last_login_date"])
        _yearfirst_login, _yearfirst_login_status = _detect_yearfirst(df["last_login_date"])
        df["days_since_last_login"] = df["last_login_date"].apply(
            lambda v: _days_since(v, dayfirst=_dayfirst_login, yearfirst=_yearfirst_login, reference_datetime=reference_datetime, column_convention_status=_combine_convention_status(_login_date_status, _yearfirst_login_status))
        )
        # Fiabilité maximale : distingue une convention JJ/MM réellement
        # PROUVÉE par au moins une valeur de la colonne d'un simple repli
        # par défaut faute de preuve, ou d'un mélange réel de conventions
        # — pour que le rapport puisse dire honnêtement "convention
        # devinée" ou "convention mélangée" plutôt que la présenter avec
        # la même assurance qu'une conclusion réellement démontrée.
        df["last_login_date_convention_uncertain"] = _login_date_status != "proven"
        df["last_login_date_convention_status"] = _login_date_status
    else:
        logger.warning("Colonne 'last_login_date' absente : détection de dormance désactivée.")
        df["days_since_last_login"] = None
        df["last_login_date_convention_uncertain"] = False
        df["last_login_date_convention_status"] = None

    # Une date de dernière connexion dans le futur (par rapport à la date
    # de référence) est une anomalie de données à part entière pour un
    # contrôle IAM — pas juste une valeur "non dormante" comme une autre.
    # Signalée séparément plutôt que silencieusement absorbée dans un
    # simple "pas dormant" qui masquerait le problème de donnée sous-jacent.
    df["last_login_future"] = df["days_since_last_login"].apply(
        lambda d: d is not None and d < 0
    )

    # Un compte sans aucune date de dernière connexion ('Never Logon Status')
    # n'est pas un cas à exclure de la détection — c'est au contraire le cas
    # le plus net de dormance : le compte n'a jamais servi depuis sa
    # création. Sans cette règle, ces comptes échappaient entièrement au
    # contrôle de dormance faute de date à comparer au seuil. Certains
    # exports encodent ce même fait par un texte littéral ('Never', 'N/A',
    # 'Jamais'...) plutôt qu'une case vide — traité de façon identique.
    NEVER_LOGGED_IN_MARKERS = {
        "never", "n/a", "na", "jamais", "none", "-", "aucune", "aucun",
        "no data", "never logged in", "aucune donnée", "aucune donnee",
        "no info", "no information",
    }
    if "last_login_date" in df.columns:
        stripped_lower = df["last_login_date"].astype(str).str.strip().str.lower()
        never_logged_in = (
            df["last_login_date"].isna()
            | (stripped_lower == "")
            | stripped_lower.isin(NEVER_LOGGED_IN_MARKERS)
        )
        df.attrs["_login_data_present"] = True
    else:
        never_logged_in = pd.Series(False, index=df.index)
        df.attrs["_login_data_present"] = False

    # ── Jours pré-calculés dans le fichier source ─────────────────────────────
    # Certains exports (Oracle BIB, TABS, etc.) fournissent directement
    # "Days Since Last Login" pré-calculé.
    # ATTENTION : on ne l'utilise QUE quand la date brute (last_login_date) est
    # absente. Si la date brute est présente, on recalcule TOUJOURS depuis cette
    # date avec la date d'extraction fournie par l'utilisateur — les jours
    # pré-calculés peuvent avoir été calculés à une date différente (ex. un export
    # AD généré en 2026 sur des données de 2025 aura des jours calculés en 2026).
    if "days_since_last_login_precomputed" in df.columns:
        precomputed = pd.to_numeric(df["days_since_last_login_precomputed"], errors="coerce")
        has_precomputed = precomputed.notna()
        # N'utiliser le pré-calculé que pour les lignes sans date brute exploitable
        already_calculated = df["days_since_last_login"].notna()
        use_precomputed = has_precomputed & ~already_calculated
        if use_precomputed.any():
            df.loc[use_precomputed, "days_since_last_login"] = precomputed[use_precomputed]
            logger.info(
                f"Jours pré-calculés utilisés en fallback pour "
                f"{int(use_precomputed.sum())} compte(s) sans date brute."
            )

    df["is_dormant"] = df["days_since_last_login"].apply(
        lambda d: d is not None and d > dormant_threshold_days
    )

    # Compte dont la date n'a pas pu être décodée (format inconnu, valeur
    # tronquée, etc.) MAIS une valeur était présente — connexion a eu lieu,
    # date inconnue → signalé comme dormant par prudence (pire cas audit).
    # EXCEPTION : les comptes avec date VIDE / blank → "jamais connecté",
    # pas "dormant". L'utilisateur veut les voir dans is_never_used, pas
    # dans is_dormant. On ne propage PAS last_login_date_unparseable aux
    # comptes vides — seulement aux comptes avec date présente mais illisible.
    if "last_login_date" in df.columns:
        raw_present = (
            df["last_login_date"].notna()
            & (df["last_login_date"].astype(str).str.strip() != "")
            & ~df["last_login_date"].astype(str).str.strip().str.lower().isin(NEVER_LOGGED_IN_MARKERS)
        )
        df["last_login_date_unparseable"] = (
            raw_present & ~never_logged_in & df["days_since_last_login"].isna()
        )
        # Uniquement les comptes avec date PRÉSENTE mais illisible → dormant par défaut
        # Les comptes BLANK restent dans never_logged_in → is_never_used
        df["is_dormant"] = df["is_dormant"] | df["last_login_date_unparseable"]
    else:
        df["last_login_date_unparseable"] = False

    # Distinction du référentiel (contrôles 2 et 6) : "Dormant" suppose
    # une connexion déjà survenue, simplement ancienne ; "Never Used" est
    # un compte qui n'a JAMAIS servi depuis sa création — un signal
    # différent (accès jamais activé plutôt qu'oublié), qui mérite son
    # propre contrôle plutôt que d'être noyé dans les mêmes dormants.
    if "account_created_date" in df.columns:
        _dayfirst_created, _created_date_status = _detect_dayfirst(df["account_created_date"])
        _yearfirst_created, _yearfirst_created_status = _detect_yearfirst(df["account_created_date"])
        df["days_since_creation"] = df["account_created_date"].apply(
            lambda v: _days_since(v, dayfirst=_dayfirst_created, yearfirst=_yearfirst_created, reference_datetime=reference_datetime, column_convention_status=_combine_convention_status(_created_date_status, _yearfirst_created_status))
        )
        days_since_creation = df["days_since_creation"]
        df["is_never_used"] = never_logged_in & days_since_creation.apply(
            lambda d: d is None or d > never_used_threshold_days
        )
    else:
        df["days_since_creation"] = None
        # Sans date de création, impossible de vérifier la règle des 30
        # jours à la lettre — on retient quand même le signal "jamais
        # connecté" plutôt que de le perdre, par sécurité (mieux vaut
        # signaler un compte qui s'avère finalement récent que d'en
        # laisser passer un vraiment jamais utilisé).
        df["is_never_used"] = never_logged_in

    # Contrôle 10 "Accounts created" : calculé DIRECTEMENT depuis la date
    # de création quand elle est disponible — un compte créé dans les 90
    # derniers jours (depuis la date d'extraction, pas nécessairement
    # aujourd'hui) n'a besoin d'aucune revue précédente pour être identifié.
    # Sans 'account_created_date', ce drapeau reste à False (repli sur la
    # comparaison avec une revue précédente, gérée séparément par
    # reporting/export.py — les deux méthodes ne sont pas redondantes,
    # chacune couvre un cas où l'autre est impossible).
    if "account_created_date" in df.columns:
        df["is_recently_created"] = df["days_since_creation"].apply(
            lambda d: d is not None and 0 <= d <= recently_created_threshold_days
        )
    else:
        df["is_recently_created"] = False

    # Un compte verrouillé (locked) n'est pas un compte "dormant" au sens
    # du contrôle standard ("Accounts that are in ACTIVE status but were
    # last logged in more than 90 days ago") : il est déjà bloqué, sans
    # risque d'usage immédiat, contrairement à un compte actif oublié.
    # Le mélanger aux vrais dormants diluerait la priorité réelle. Suivi
    # séparément (is_locked) plutôt qu'ignoré : un compte verrouillé
    # depuis longtemps reste un sujet de nettoyage à part entière.
    #
    # Frontière de mot (\b) plutôt qu'une simple sous-chaîne : sans elle,
    # 'Unlocked' (l'inverse exact !), 'Déverrouillé' ou 'Débloqué'
    # contiennent respectivement 'locked'/'verrouillé'/'bloqué' comme
    # sous-chaîne et seraient signalés à tort comme verrouillés — un
    # faux positif sérieux puisque ces statuts signifient précisément le
    # contraire.
    LOCKED_MARKERS_RE = re.compile(
        r"\b(locked|verrouill[ée]|bloqu[ée])\b", re.IGNORECASE
    )
    # Source 1 : détection via les valeurs de account_status (ex. "Locked")
    if "account_status" in df.columns:
        status_lower = df["account_status"].astype(str).fillna("").str.strip()
        is_locked_from_status = status_lower.apply(lambda s: bool(LOCKED_MARKERS_RE.search(s)))
    else:
        is_locked_from_status = pd.Series(False, index=df.index)

    # Source 2 : colonne dédiée is_locked (ex. "LOCKED" = Yes/No)
    # Cas fréquent : un fichier a DEUX colonnes liées au statut —
    # "STATUS" = Active/Inactive et "LOCKED" = Yes/No — les deux sont
    # combinées. La colonne peut être mappée manuellement dans le dashboard.
    if "is_locked" in df.columns:
        is_locked_from_col = df["is_locked"].apply(
            lambda v: str(v).strip().lower() in {
                "yes", "true", "1", "oui", "locked", "verrouillé", "verrouille", "y"
            }
        )
    else:
        is_locked_from_col = pd.Series(False, index=df.index)

    df["is_locked"] = is_locked_from_status | is_locked_from_col

    if "account_status" in df.columns:
        # 1. D'abord les mappings appris manuellement (mémorisés via le dashboard)
        # 2. Ensuite la reconnaissance automatique (liste ACTIVE_STATUS_VALUES +
        #    tokenisation pour les formats composés ex. 'Y-Active')
        # 3. Si toujours inconnu → PIRE CAS = traité comme POTENTIELLEMENT ACTIF
        #    Raison : un compte dont on ne sait pas s'il est actif ou non doit
        #    être contrôlé plutôt qu'ignoré — la direction inverse (le traiter
        #    comme inactif) ferait passer des comptes actifs à travers les mailles
        #    sans jamais être revus. Résultat annoté dans la colonne 'status_resolved'
        #    pour que le dashboard puisse afficher les valeurs inconnues et proposer
        #    de les mapper manuellement.
        def _resolve_with_custom(val) -> tuple[bool, bool]:
            """Retourne (is_active, is_unknown)."""
            from ingestion.custom_status_mappings import _normalize as _norm_status
            if custom_status_mappings:
                key = _norm_status(str(val))
                if key in custom_status_mappings:
                    return custom_status_mappings[key] == "active", False
            if _is_active_account(val):
                return True, False
            norm = str(val).strip().lower()
            tokens = _tokenize_status_value(val)
            # LOCKED = DISABLED (retour terrain BSS/MTN) — vérifié avant le
            # bloc générique TERMINATED_STATUS_VALUES car 'locked' est dans les
            # deux ensembles ; on veut le résultat "inactif" ici.
            if norm in LOCKED_STATUS_VALUES or (tokens & LOCKED_STATUS_VALUES):
                return False, False
            if norm in TERMINATED_STATUS_VALUES or (tokens & TERMINATED_STATUS_VALUES):
                return False, False
            if not norm or norm in ("nan", "none", ""):
                # Champ vide = actif dans certains systèmes (ex. BSS où la
                # colonne identity/accountState vide signifie "unlock/actif").
                return True, False
            # Vraiment inconnue → pire cas (potentiellement actif)
            return True, True

        resolved = df["account_status"].apply(_resolve_with_custom)
        is_active_status = resolved.apply(lambda x: x[0])
        is_unknown_status = resolved.apply(lambda x: x[1])
        df["status_is_unknown"] = is_unknown_status
        # Conserver is_active_status comme colonne booléenne utilisable
        # par les rapports PDF/Word pour filtrer les comptes actifs avec
        # respect des mappings personnalisés (ex. EXPIRED mappé en "active").
        df["is_active_for_audit"] = is_active_status
        # Collecte des valeurs inconnues distinctes pour le dashboard
        unknown_vals = df.loc[is_unknown_status, "account_status"].astype(str).str.strip().unique().tolist()
        df.attrs["unknown_status_values"] = unknown_vals

        # Toutes les valeurs distinctes avec leur interprétation actuelle —
        # pas seulement les inconnues. Permet au dashboard d'afficher un
        # tableau complet où chaque valeur est visible avec son résultat
        # (actif/inactif/verrouillé), et l'utilisateur peut corriger
        # n'importe laquelle, même une qui a été interprétée automatiquement
        # mais de travers (ex. 'offline & locked' → contient 'locked' →
        # traité comme actif, alors que l'auditeur veut l'exclure).
        all_status_vals = df["account_status"].astype(str).fillna("").str.strip().unique().tolist()
        all_status_interpretation = {}
        for v in all_status_vals:
            if not v:
                continue
            r = _resolve_with_custom(v)
            is_act, is_unk = r
            lk = bool(LOCKED_MARKERS_RE.search(str(v)))
            if is_unk:
                label = "⚠️ Unknown (treated as Active)"
            elif lk:
                label = "🔒 Locked (→ Disabled)"
            elif is_act:
                label = "🟢 Active"
            else:
                label = "🔴 Disabled"
            all_status_interpretation[v] = label
        df.attrs["all_status_values"] = all_status_interpretation

        if unknown_vals:
            logger.warning(
                f"Valeurs de statut non reconnues, traitées comme POTENTIELLEMENT ACTIVES "
                f"(pire cas audit) : {unknown_vals}. Associez-les dans le dashboard pour "
                f"affiner l'analyse."
            )
        df["is_dormant"] = df["is_dormant"] & is_active_status
        df["is_never_used"] = df["is_never_used"] & is_active_status
    else:
        df["is_locked"] = False
        df["status_is_unknown"] = False
        df.attrs["unknown_status_values"] = []
        df.attrs["all_status_values"] = {}

    if "account_status" in df.columns and "employee_status" in df.columns:
        df["is_terminated_but_active"] = df.apply(
            lambda r: _is_active_account(r["account_status"])
            and _is_terminated_employee(r["employee_status"]),
            axis=1,
        )
        df.attrs["_hr_data_present"] = True
    else:
        logger.warning(
            "Colonnes 'account_status' et/ou 'employee_status' absentes : "
            "détection des comptes orphelins post-départ désactivée."
        )
        df["is_terminated_but_active"] = False
        df.attrs["_hr_data_present"] = False


    privileged_from_flag = pd.Series(False, index=df.index)
    if "is_privileged" in df.columns:
        privileged_from_flag = df["is_privileged"].apply(_is_privileged)

    privileged_from_role = pd.Series(False, index=df.index)
    for priv_col in ("role", "user_rights"):
        if priv_col in df.columns:
            col_data = df[priv_col].astype(str).fillna("").str.strip()
            from_col = col_data.apply(
                lambda raw: any(
                    _is_privileged_role_value(r.strip())
                    for r in (raw.split("\n") if "\n" in raw
                              else raw.split(";") if ";" in raw
                              else raw.split(","))
                    if r.strip()
                )
            )
            privileged_from_role = privileged_from_role | from_col

    # Troisième source : droits qualifiés manuellement comme "admin" dans
    # le dashboard (custom_rights_mappings). Permet de marquer un droit
    # spécifique (ex. "MTN_INV_Ajustement") comme admin sans modifier le
    # code, pour des droits propres à un OPCO que les mots-clés génériques
    # ne peuvent pas deviner.
    privileged_from_custom_rights = pd.Series(False, index=df.index)
    if custom_rights_mappings:
        for priv_col in ("user_rights", "role"):
            if priv_col in df.columns:
                col_data = df[priv_col].astype(str).fillna("").str.strip()
                privileged_from_custom_rights = privileged_from_custom_rights | col_data.apply(
                    lambda raw: any(
                        custom_rights_mappings.get(r.strip()) == "admin"
                        for r in (raw.split("\n") if "\n" in raw
                                  else raw.split(";") if ";" in raw
                                  else raw.split(","))
                        if r.strip()
                    )
                )

    df["is_privileged_flag"] = privileged_from_flag | privileged_from_role | privileged_from_custom_rights

    if "manager" in df.columns:
        df["has_no_manager"] = df["manager"].isna() | (df["manager"].astype(str).str.strip() == "")
    else:
        df["has_no_manager"] = False

    if "password_last_set" in df.columns:
        _dayfirst_pwd, _password_date_status = _detect_dayfirst(df["password_last_set"])
        _yearfirst_pwd, _yearfirst_pwd_status = _detect_yearfirst(df["password_last_set"])
        df["days_since_password_change"] = df["password_last_set"].apply(
            lambda v: _days_since(v, dayfirst=_dayfirst_pwd, yearfirst=_yearfirst_pwd, reference_datetime=reference_datetime, column_convention_status=_combine_convention_status(_password_date_status, _yearfirst_pwd_status))
        )
        df.attrs[PASSWORD_DATA_PRESENT_ATTR] = True
    else:
        logger.warning("Colonne 'password_last_set' absente : détection de mot de passe périmé désactivée.")
        df["days_since_password_change"] = None
        df.attrs[PASSWORD_DATA_PRESENT_ATTR] = False

    df["password_change_future"] = df["days_since_password_change"].apply(
        lambda d: d is not None and d < 0
    )

    # Fiabilité maximale, même principe que pour 'last_login_date'
    # (compte jamais connecté = signalé, pas ignoré) : une date de
    # dernier changement de mot de passe qu'on ne peut PAS déterminer —
    # marqueur explicite ('No info', 'Never', vide...) OU simplement un
    # format qu'on n'arrive pas à interpréter avec confiance — n'est PAS
    # une raison de considérer le compte comme sain par défaut. Rester
    # silencieux ici reviendrait à traiter "on ne sait pas" comme
    # "c'est bon", alors que pour un audit de sécurité, l'absence
    # d'information sur la dernière rotation d'un mot de passe est au
    # moins aussi préoccupante qu'une rotation ancienne mais connue —
    # potentiellement plus (jamais suivi, ou volontairement dissimulé).
    # Ne s'applique QUE quand la colonne existe réellement (sinon le
    # contrôle entier est désactivé plus haut, cas différent d'une
    # valeur manquante ligne par ligne au sein d'une colonne présente).
    df["password_change_unknown"] = (
        ("password_last_set" in df.columns)
        & df["days_since_password_change"].isna()
    )

    df["is_password_stale"] = df["days_since_password_change"].apply(
        lambda d: d is not None and d > password_stale_threshold_days
    )
    # Les dates inconnues sont traitées comme stale (pire cas audit)
    if "password_change_unknown" in df.columns:
        df["is_password_stale"] = df["is_password_stale"] | df["password_change_unknown"]

    if "password_status" in df.columns:
        df["has_non_expiring_password"] = df["password_status"].apply(_has_non_expiring_password)
    else:
        df["has_non_expiring_password"] = False

    # Convention de nommage standard du secteur pour les comptes de service
    # (préfixe/suffixe "svc_"), pour les distinguer des comptes humains —
    # un compte de service dormant n'appelle pas la même action qu'un
    # compte utilisateur dormant (vérification technique plutôt que
    # suppression pure et simple).
    if "username" in df.columns:
        df["is_service_account"] = df["username"].apply(_is_service_account_name)
        # Recalcul is_password_stale pour les comptes de service : seuil 365j
        # Selon la politique : password de compte de service changé annuellement
        _SERVICE_PWD_THRESHOLD = 365
        if "days_since_password_change" in df.columns:
            _svc_m = df["is_service_account"].astype(bool)
            # Flag dédié pour l'affichage en section B du Ctrl 14
            df["is_service_password_stale"] = (
                _svc_m
                & df["days_since_password_change"].apply(
                    lambda d: d is not None and d > _SERVICE_PWD_THRESHOLD
                )
            )
            if "password_change_unknown" in df.columns:
                df["is_service_password_stale"] = (
                    df["is_service_password_stale"]
                    | (_svc_m & df["password_change_unknown"])
                )
            # Remplacer is_password_stale pour les comptes de service par le seuil 365j
            df.loc[_svc_m, "is_password_stale"] = df.loc[_svc_m, "is_service_password_stale"]
        # Contrôle 4 (Test Accounts) : indice par convention de nommage
        # uniquement — jamais traité comme une certitude (voir _TEST_ACCOUNT_RE).
        df["is_test_account"] = df["username"].apply(_is_test_account_name)
        # Contrôle 3 (Orphaned Accounts) : "Accounts with active status,
        # but no information that would allow the holder to be positively
        # identified" — un nom de compte générique (admin, support,
        # service...) en est le cas type. Restreint aux comptes ACTIFS
        # (la définition du contrôle le précise explicitement) — mais
        # seulement quand le statut est CONNU et confirme explicitement
        # que le compte n'est pas actif. Un statut vide/inconnu ne doit
        # PAS faire disparaître le signal : on ne sait pas si le compte
        # est actif ou non, donc mieux vaut le signaler par prudence
        # (pire cas) que le laisser passer silencieusement — même
        # principe déjà appliqué au mot de passe non renseigné et à la
        # date de connexion illisible ailleurs dans ce module.
        is_generic_name = df["username"].apply(_is_orphaned_account_name)
        if "account_status" in df.columns:
            status_str = df["account_status"].astype(str).fillna("").str.strip()
            is_confirmed_inactive = (status_str != "") & ~df["account_status"].apply(_is_active_account)
            df["is_orphaned_account"] = is_generic_name & ~is_confirmed_inactive
        else:
            df["is_orphaned_account"] = is_generic_name
    else:
        df["is_service_account"] = False
        df["is_test_account"] = False
        df["is_orphaned_account"] = False

    # Contrôle 9 (Naming convention) : demande explicite de ne PLUS
    # calculer automatiquement — chaque OPCOs a sa propre convention de
    # nommage, une règle unique codée en dur produirait un faux signal
    # pour toutes les entités qui n'utilisent pas cette convention
    # précise. Laissé à la vérification manuelle du reviewer plutôt que
    # de risquer un score de risque ou une action recommandée basés sur
    # une hypothèse potentiellement fausse.
    df["is_non_compliant_naming"] = False

    # Comptes en doublon : la même personne détient plusieurs comptes actifs
    # pour un même usage. On approxime via le nom complet (à défaut d'un
    # identifiant employé fiable et systématiquement présent) : si un même
    # nom complet est associé à plusieurs comptes actifs sur un même
    # système, c'est un doublon à signaler.
    #
    # Regroupement sur une clé NORMALISÉE (espaces/casse), pas sur le nom
    # brut : 'Jean Dupont' et 'JEAN DUPONT' (casse différente selon le
    # système source) ou 'Jean Dupont' et ' Jean Dupont ' (espaces
    # parasites, fréquents en pratique) désignent la même personne mais
    # ne correspondraient jamais en comparaison exacte — un vrai doublon
    # passerait alors inaperçu, à l'opposé de l'objectif du contrôle 8.
    # Le nom d'affichage original (non modifié) reste utilisé partout
    # ailleurs dans les rapports.
    if "full_name" in df.columns and "account_status" in df.columns and "system" in df.columns:
        active_mask = df["account_status"].apply(_is_active_account)
        normalized_name = (
            df["full_name"].astype(str).str.strip().str.lower().str.replace(r"\s+", " ", regex=True)
        )
        # Le système lui-même est normalisé pour le regroupement (mais pas
        # pour l'affichage) : deux comptes du même système peuvent être
        # enregistrés avec une casse différente selon la source d'export
        # (ex. 'AD' puis 'ad') — sans cette normalisation, un vrai doublon
        # sur le même système passerait inaperçu, croyant à tort qu'il
        # s'agit de deux systèmes distincts.
        normalized_system = df["system"].astype(str).str.strip().str.lower()
        dup_counts = (
            df[active_mask]
            .assign(_normalized_name=normalized_name[active_mask], _normalized_system=normalized_system[active_mask])
            .groupby(["_normalized_name", "_normalized_system"])["username"]
            .transform("nunique")
        )
        df["is_duplicate_account"] = False
        df.loc[active_mask, "is_duplicate_account"] = dup_counts.reindex(df.index[active_mask]).fillna(0) > 1
    else:
        df["is_duplicate_account"] = False

    # Cohérence temporelle : un compte ne peut pas s'être connecté (ou
    # avoir changé son mot de passe) AVANT sa propre date de création —
    # une date valide syntaxiquement peut rester absurde métier. "Jours
    # écoulés" (days_since) est PLUS GRAND pour une date PLUS ANCIENNE :
    # une connexion/un changement de mot de passe dont l'ancienneté
    # dépasse celle de la création daterait donc d'avant la création —
    # impossible ; signalé comme anomalie de qualité de donnée plutôt que
    # silencieusement ignoré.
    df["temporal_inconsistency"] = False
    if "days_since_creation" in df.columns:
        creation = df["days_since_creation"]
        if "days_since_last_login" in df.columns:
            login = df["days_since_last_login"]
            both_known = creation.notna() & login.notna()
            df.loc[both_known, "temporal_inconsistency"] |= (
                login[both_known] > creation[both_known]
            )
        if "days_since_password_change" in df.columns:
            pwd = df["days_since_password_change"]
            both_known = creation.notna() & pwd.notna()
            df.loc[both_known, "temporal_inconsistency"] |= (
                pwd[both_known] > creation[both_known]
            )

    df["review_action"] = df.apply(_determine_action, axis=1)
    df["risk_level"] = df.apply(_determine_risk_level, axis=1)
    _score_and_reasons = df.apply(_compute_risk_score, axis=1)
    df["risk_score"] = _score_and_reasons.apply(lambda t: t[0])
    df["risk_score_reasons"] = _score_and_reasons.apply(lambda t: t[1])

    logger.info(
        "Analyse terminée. Répartition des actions :\n"
        f"{df['review_action'].value_counts().to_string()}"
    )

    return df


def _determine_action(row) -> str:
    if row["is_terminated_but_active"]:
        return "Révoquer immédiatement"
    if (row["is_dormant"] or row["is_never_used"]) and row["is_privileged_flag"]:
        return "Désactiver (privilégié dormant)"
    if row["is_privileged_flag"] and row["has_non_expiring_password"]:
        return "Forcer l'expiration du mot de passe (privilégié)"
    if (row["is_dormant"] or row["is_never_used"]) and row.get("is_service_account", False):
        # Un compte de service dormant s'analyse différemment d'un compte
        # humain : vérifier auprès du propriétaire technique avant toute
        # décision, plutôt qu'une désactivation directe qui pourrait casser
        # un processus automatisé encore utilisé.
        return "Vérifier avec le propriétaire technique (compte de service)"
    if row["is_dormant"]:
        if row.get("last_login_date_unparseable", False):
            # Distinction honnête : on ne SAIT PAS que ce compte est
            # réellement inactif depuis plus que le seuil — seulement que
            # sa date de dernière connexion n'est pas exploitable telle
            # quelle (format tronqué, ex. '4 20:09:01 +0000 2025' sans
            # jour de semaine ni mois). Une désactivation directe
            # prétendrait à une certitude qu'on n'a pas ; vérifier la
            # date source est la bonne première étape.
            return "Vérifier (date de dernière connexion non exploitable)"
        return "Désactiver (dormant)"
    if row["is_never_used"]:
        # Distinct du dormant classique : ce compte n'a JAMAIS servi
        # depuis sa création (contrôle 6), pas juste oublié après usage —
        # signal utile à garder visible séparément pour l'audit.
        return "Désactiver (jamais utilisé)"
    if row.get("is_duplicate_account", False):
        return "Fusionner les doublons (ne garder qu'un compte actif)"
    if row["is_password_stale"] and row.get("is_service_account", False):
        # Le contrôle 14 du référentiel exclut explicitement les comptes
        # de service de la règle de rotation standard ("Except service
        # account, All accounts that the age exceed 90 days must be
        # changed or disable") : la rotation forcée casserait un
        # processus automatisé encore utilisé sans qu'un humain n'ait pu
        # s'en rendre compte. Vérification auprès du propriétaire
        # technique plutôt qu'exigence de changement direct.
        return "Vérifier avec le propriétaire technique (mot de passe, compte de service)"
    if row["is_password_stale"]:
        return "Exiger un changement de mot de passe"
    if row["has_no_manager"]:
        return "Identifier un owner"
    if row.get("is_locked", False):
        # Priorité basse (pas d'usage possible tant que verrouillé), mais
        # visible plutôt que silencieusement ignoré : un compte verrouillé
        # de longue date doit être formellement nettoyé (supprimé ou
        # réactivé après vérification), pas laissé indéfiniment en l'état.
        return "Nettoyer (compte verrouillé)"
    if row.get("is_test_account", False):
        # Indice de nommage seul (contrôle 4) : jamais une désactivation
        # automatique, juste une vérification — beaucoup de vrais comptes
        # légitimes peuvent contenir ces motifs par coïncidence.
        return "Vérifier (compte de test présumé)"
    if row.get("is_orphaned_account", False):
        # Contrôle 3 : même prudence que pour le compte de test — un nom
        # générique est un indice, pas une certitude (un vrai compte
        # métier peut légitimement contenir un de ces mots). Le
        # référentiel demande un renommage "quand possible", pas une
        # désactivation directe.
        return "Vérifier (compte générique/orphelin présumé)"
    if row.get("is_non_compliant_naming", False):
        return "Renommer selon la convention"
    return "Aucune action"


def _compute_risk_score(row) -> tuple[int, list[str]]:
    """
    Score de risque 0-100, additif et plafonné, avec le détail des
    raisons qui le composent — objectif de traçabilité d'audit ("pourquoi
    ce score ?"), complémentaire du niveau catégoriel (Critique/Élevé/
    Moyen/Faible) déjà utilisé partout ailleurs dans le rapport, sans le
    remplacer.
    """
    score = 0
    reasons = []

    if row["is_terminated_but_active"]:
        score += 50
        reasons.append(("Employé parti, compte encore actif", 50))
    if row["is_dormant"] or row.get("is_never_used", False):
        score += 20
        if row.get("last_login_date_unparseable", False):
            reasons.append(("Date de dernière connexion non exploitable (format tronqué)", 20))
        else:
            reasons.append(("Compte dormant ou jamais utilisé", 20))
    if row["is_privileged_flag"]:
        score += 30
        reasons.append(("Compte privilégié", 30))
    if row["is_privileged_flag"] and row.get("has_non_expiring_password", False):
        score += 25
        reasons.append(("Mot de passe n'expirant jamais (privilégié)", 25))
    if row["is_password_stale"] and not row.get("is_service_account", False):
        score += 20
        if row.get("password_change_unknown", False):
            # Distinction honnête : on ne SAIT PAS que le seuil est
            # dépassé ici, seulement qu'on ne peut pas le vérifier — même
            # niveau de risque retenu (l'absence d'info est au moins
            # aussi préoccupante), mais le libellé ne doit pas prétendre
            # à une certitude qu'on n'a pas.
            reasons.append(("Dernier changement de mot de passe inconnu (non vérifiable)", 20))
        else:
            reasons.append(("Mot de passe périmé (> seuil retenu)", 20))
    if row["has_no_manager"]:
        score += 15
        reasons.append(("Aucun manager/owner identifié", 15))
    if row.get("is_duplicate_account", False):
        score += 15
        reasons.append(("Compte en doublon", 15))
    if row.get("is_locked", False):
        score += 5
        reasons.append(("Compte verrouillé", 5))
    if row.get("is_test_account", False):
        score += 10
        reasons.append(("Nom évoquant un compte de test", 10))
    if row.get("is_non_compliant_naming", False):
        score += 5
        reasons.append(("Nom non conforme à la convention", 5))

    return min(score, 100), reasons


def _determine_risk_level(row) -> str:
    # Cohérent avec l'exclusion des comptes de service pour la rotation
    # de mot de passe (contrôle 14) : un mot de passe périmé ne doit pas
    # non plus faire monter le niveau de risque pour ces comptes, sans
    # quoi l'exclusion serait incomplète (action différente, mais risque
    # identique à un compte humain).
    password_stale_relevant = row["is_password_stale"] and not row.get("is_service_account", False)
    dormant_or_never_used = row["is_dormant"] or row["is_never_used"]

    if row["is_terminated_but_active"]:
        return "Critique"
    if dormant_or_never_used and row["is_privileged_flag"]:
        return "Critique"
    if row["is_privileged_flag"] and row["has_non_expiring_password"]:
        return "Critique"
    if dormant_or_never_used or row["has_no_manager"] or password_stale_relevant or row.get("is_duplicate_account", False):
        return "Élevé" if row["is_privileged_flag"] else "Moyen"
    return "Faible"


def summarize(df: pd.DataFrame) -> dict:
    """Retourne un résumé chiffré de l'analyse, utile pour un dashboard/rapport."""
    return {
        "total_accounts": len(df),
        "terminated_but_active": int(df["is_terminated_but_active"].sum()),
        "dormant_accounts": int(df["is_dormant"].sum()),
        "never_used_accounts": int(df.get("is_never_used", pd.Series(dtype=bool)).sum()),
        "privileged_accounts": int(df["is_privileged_flag"].sum()),
        "privileged_dormant": int((df["is_dormant"] & df["is_privileged_flag"]).sum()),
        "accounts_without_manager": int(df["has_no_manager"].sum()),
        "password_stale": int(df["is_password_stale"].sum()),
        "privileged_non_expiring_password": int(
            (df["is_privileged_flag"] & df["has_non_expiring_password"]).sum()
        ),
        "service_accounts": int(df.get("is_service_account", pd.Series(dtype=bool)).sum()),
        "duplicate_accounts": int(df.get("is_duplicate_account", pd.Series(dtype=bool)).sum()),
        "critical_risk": int((df["risk_level"] == "Critique").sum()),
        "temporal_inconsistencies": int(df.get("temporal_inconsistency", pd.Series(dtype=bool)).sum()),
        "future_dates": int(
            (
                df.get("last_login_future", pd.Series(dtype=bool))
                | df.get("password_change_future", pd.Series(dtype=bool))
            ).sum()
        ),
        # Fiabilité maximale sur les dates : True si la convention JJ/MM a
        # dû être devinée par défaut faute de toute preuve dans la
        # colonne (pas si elle a été réellement démontrée) — un seul
        # indicateur au niveau du fichier plutôt que par ligne, puisque
        # la convention s'applique à toute la colonne uniformément.
        "date_convention_uncertain": bool(
            df.get("last_login_date_convention_uncertain", pd.Series([False])).iloc[0]
        ) if len(df) else False,
    }


if __name__ == "__main__":
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent.parent))

    from ingestion.ingest import load_file

    if len(sys.argv) < 2:
        print("Usage : python -m analysis.access_review <chemin_fichier>")
        sys.exit(1)

    df_in = load_file(sys.argv[1])
    df_out = analyze_access(df_in)

    cols = [
        c for c in [
            "username", "full_name", "system", "account_status",
            "employee_status", "days_since_last_login", "is_privileged_flag",
            "review_action", "risk_level",
        ] if c in df_out.columns
    ]
    print(df_out[cols].sort_values("risk_level"))
    print("\nRésumé :", summarize(df_out))
