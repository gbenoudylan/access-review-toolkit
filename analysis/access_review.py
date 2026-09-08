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

DORMANT_THRESHOLD_DAYS = 90  # seuil standard du secteur (souvent 60-90 jours)
PASSWORD_STALE_THRESHOLD_DAYS = 90  # standard interne MTN : 90 jours pour les comptes standards

ACTIVE_STATUS_VALUES = {"active", "actif", "enabled", "activé", "oui", "yes", "true", "1", "open"}
TERMINATED_STATUS_VALUES = {
    "terminated", "termine", "terminé", "parti", "departed", "left",
    "inactive", "inactif", "resigned", "démissionné",
    "retired", "retraité", "leaver", "ex-employee", "former employee",
    "fired", "dismissed", "licencié", "licencie", "no longer employed",
    "not employed", "separated", "redundant",
}
PRIVILEGED_VALUES = {"oui", "yes", "true", "1", "admin", "administrateur"}
NEVER_EXPIRES_VALUES = {"never expires", "n'expire jamais", "never", "jamais"}

# Format "Generalized Time" utilisé par LDAP/Active Directory pour les dates
# (ex. whenChanged, whenCreated) : YYYYMMDDHHMMSS[.f]Z — non reconnu
# automatiquement par le parseur de dates générique de pandas.
_LDAP_GENERALIZED_TIME_RE = re.compile(r"^(\d{14})(\.\d+)?Z?$")
# Nombre isolé (sans jour de semaine ni mois) suivi d'une heure, d'un
# fuseau horaire explicite et d'une année — ex. '4 20:09:01 +0000 2025'.
# Rencontré en pratique sur des exports où le jour de semaine ET le mois
# ont été tronqués, ne laissant que le jour du mois. Sans le mois, la
# date réelle est indéterminable ; pandas devinerait sinon ce nombre
# comme un MOIS avec un jour arbitraire (voir _days_since).
_TRUNCATED_DATE_RE = re.compile(r"^\d{1,2}\s+\d{1,2}:\d{2}:\d{2}\s+[+-]\d{4}\s+\d{4}$")


def _is_active_account(value) -> bool:
    if value is None:
        return False
    return str(value).strip().lower() in ACTIVE_STATUS_VALUES


def _is_terminated_employee(value) -> bool:
    if value is None:
        return False
    return str(value).strip().lower() in TERMINATED_STATUS_VALUES


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
    r"(^|[_\-\.])(test|uat|qa|dummy|demo|sandbox)(ing)?([_\-\.]|\d|$)", re.IGNORECASE
)


def _is_test_account_name(username) -> bool:
    if username is None:
        return False
    return bool(_TEST_ACCOUNT_RE.search(str(username).strip()))


def _normalize_for_naming_check(text: str) -> str:
    """Retire accents/espaces/tirets/apostrophes pour une comparaison
    tolérante — sans quoi un nom africain/français accentué ('Ébénézer',
    'N'Guessan') serait signalé à tort comme non conforme."""
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[\s\-']", "", text).lower()


def _expected_username_first_last(first_name: str, last_name: str) -> str | None:
    """
    Contrôle 9 (Naming convention) — règle du référentiel : 'première
    lettre du prénom + nom de famille' (ex. Michael Brown -> mbrown).
    Retourne None si l'un des deux champs est vide (pas assez
    d'information pour une comparaison fiable).
    """
    first_name = str(first_name).strip() if first_name else ""
    last_name = str(last_name).strip() if last_name else ""
    if not first_name or not last_name:
        return None
    return _normalize_for_naming_check(first_name[0] + last_name)


def _check_naming_convention(row) -> bool | None:
    """
    True si le compte NE respecte PAS la convention attendue, False si
    conforme, None si non vérifiable (infos manquantes) — à distinguer
    d'un vrai résultat "conforme".
    """
    first_name, last_name = None, None
    if row.get("first_name") and row.get("last_name"):
        first_name, last_name = row["first_name"], row["last_name"]
    elif row.get("full_name"):
        parts = str(row["full_name"]).strip().split()
        if len(parts) >= 2:
            first_name, last_name = parts[0], parts[-1]

    expected = _expected_username_first_last(first_name, last_name) if first_name else None
    if expected is None or not row.get("username"):
        return None

    actual = _normalize_for_naming_check(str(row["username"]))
    # Tolère un suffixe numérique (doublons légitimes : jdupont, jdupont2...)
    actual_no_suffix = re.sub(r"\d+$", "", actual)
    return actual != expected and actual_no_suffix != expected


_AMBIGUOUS_DATE_START_RE = re.compile(r"^(\d{1,2})[/-](\d{1,2})[/-]\d{2,4}")


def _detect_dayfirst(series: pd.Series) -> bool:
    """
    Détermine si une colonne de dates au format 'A/B/Année' (non ISO,
    donc potentiellement ambigu) doit être lue jour-premier (JJ/MM,
    standard francophone/africain) ou mois-premier (MM/JJ, standard
    américain — ex. un export venant d'un outil IAM/SIEM américain,
    rencontré en pratique même dans un contexte MTN).

    Un même fichier utilise presque toujours une convention cohérente sur
    toute sa colonne : on cherche donc, dans les valeurs réellement
    présentes, au moins UNE valeur qui lève l'ambiguïté (un groupe > 12,
    qui ne peut donc pas être un mois) plutôt que de deviner à l'aveugle.
    Cette preuve, trouvée une seule fois, s'applique à toute la colonne.
    Si aucune valeur ne permet de trancher (tous les groupes <= 12 partout,
    par simple coïncidence ou petit échantillon), on retombe sur
    jour-premier par défaut (biais assumé vers le standard MTN).
    """
    for value in series.dropna():
        match = _AMBIGUOUS_DATE_START_RE.match(str(value).strip())
        if not match:
            continue
        first, second = int(match.group(1)), int(match.group(2))
        if first > 12:
            return True  # le 1er groupe ne peut être qu'un jour -> jour-premier confirmé
        if second > 12:
            return False  # le 2nd groupe ne peut être qu'un jour -> mois-premier confirmé
    return True  # aucune preuve trouvée : repli par défaut (standard MTN)


def _detect_yearfirst(series: pd.Series) -> bool:
    """
    Détermine si une colonne 'A-B-C' à année sur 2 chiffres place l'année
    en PREMIER (ex. '26-01-15' pour le 15/01/2026) plutôt qu'en dernier
    (ex. '15-01-26' pour la même date, ordre JJ-MM-AA ou MM-JJ-AA). Le
    groupe du milieu est toujours le mois quelle que soit la convention ;
    seule la position de l'année (1er ou 3e groupe) reste à déterminer.

    Un 1er groupe > 31 ne peut être qu'une année (aucun jour ne dépasse
    31) : preuve directe et suffisante, cherchée dans toute la colonne.
    Sans cette preuve, l'ambiguïté reste entière quand le 1er groupe est
    un nombre à la fois plausible comme jour ET comme année à 2 chiffres
    (ex. '26') — dans ce cas, on ne force PAS yearfirst (on laisse
    _detect_dayfirst trancher jour/mois comme pour un format classique à
    année sur 4 chiffres ou en dernière position).
    """
    for value in series.dropna():
        match = _AMBIGUOUS_DATE_START_RE.match(str(value).strip())
        if not match:
            continue
        if int(match.group(1)) > 31:
            return True
    return False


def _days_since(date_value, dayfirst: bool = True, yearfirst: bool = False) -> float | None:
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
    """
    if pd.isna(date_value) or date_value is None:
        return None

    text_value = str(date_value).strip()

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

    ldap_match = _LDAP_GENERALIZED_TIME_RE.match(text_value)
    if ldap_match:
        try:
            parsed_dt = datetime.strptime(ldap_match.group(1), "%Y%m%d%H%M%S")
            return (datetime.now() - parsed_dt).days
        except ValueError:
            return None

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
            return (datetime.now() - parsed_dt).days
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
    year_first_4digit = bool(re.match(r"^\d{4}[-/]", text_value))
    year_first = year_first_4digit or yearfirst

    try:
        import warnings
        with warnings.catch_warnings():
            # dayfirst=True est sans effet sur un format déjà non ambigu
            # (ISO, ou timestamp Excel/pandas) ; pandas émet un avertissement
            # informatif dans ce cas précis, sans rapport avec un vrai risque
            # d'erreur — supprimé ici pour ne pas polluer les journaux.
            warnings.filterwarnings("ignore", message=".*dayfirst.*")
            parsed = pd.to_datetime(
                date_value, errors="coerce",
                dayfirst=dayfirst and not year_first,
                yearfirst=year_first and not year_first_4digit,
            )
        if pd.isna(parsed):
            return None
        return (datetime.now() - parsed.to_pydatetime().replace(tzinfo=None)).days
    except Exception:
        return None


def analyze_access(
    df: pd.DataFrame,
    dormant_threshold_days: int = DORMANT_THRESHOLD_DAYS,
    password_stale_threshold_days: int = PASSWORD_STALE_THRESHOLD_DAYS,
    never_used_threshold_days: int = 30,
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

    if "last_login_date" in df.columns:
        _truncated_count = df["last_login_date"].astype(str).str.match(_TRUNCATED_DATE_RE).sum()
        if _truncated_count:
            logger.warning(
                f"{_truncated_count} date(s) de dernière connexion au format tronqué "
                f"(jour de semaine et mois manquants, ex. '4 20:09:01 +0000 2025') — "
                f"non exploitables, à corriger à la source plutôt que devinées. "
                f"Ces comptes ne sont ni comptés dormants ni exclus : leur ancienneté "
                f"réelle de connexion reste simplement inconnue."
            )
        _dayfirst_login = _detect_dayfirst(df["last_login_date"])
        _yearfirst_login = _detect_yearfirst(df["last_login_date"])
        df["days_since_last_login"] = df["last_login_date"].apply(
            lambda v: _days_since(v, dayfirst=_dayfirst_login, yearfirst=_yearfirst_login)
        )
    else:
        logger.warning("Colonne 'last_login_date' absente : détection de dormance désactivée.")
        df["days_since_last_login"] = None

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
    }
    if "last_login_date" in df.columns:
        stripped_lower = df["last_login_date"].astype(str).str.strip().str.lower()
        never_logged_in = (
            df["last_login_date"].isna()
            | (stripped_lower == "")
            | stripped_lower.isin(NEVER_LOGGED_IN_MARKERS)
        )
    else:
        never_logged_in = pd.Series(False, index=df.index)

    df["is_dormant"] = df["days_since_last_login"].apply(
        lambda d: d is not None and d > dormant_threshold_days
    )

    # Distinction du référentiel (contrôles 2 et 6) : "Dormant" suppose
    # une connexion déjà survenue, simplement ancienne ; "Never Used" est
    # un compte qui n'a JAMAIS servi depuis sa création — un signal
    # différent (accès jamais activé plutôt qu'oublié), qui mérite son
    # propre contrôle plutôt que d'être noyé dans les mêmes dormants.
    if "account_created_date" in df.columns:
        _dayfirst_created = _detect_dayfirst(df["account_created_date"])
        _yearfirst_created = _detect_yearfirst(df["account_created_date"])
        days_since_creation = df["account_created_date"].apply(
            lambda v: _days_since(v, dayfirst=_dayfirst_created, yearfirst=_yearfirst_created)
        )
        df["is_never_used"] = never_logged_in & days_since_creation.apply(
            lambda d: d is None or d > never_used_threshold_days
        )
    else:
        # Sans date de création, impossible de vérifier la règle des 30
        # jours à la lettre — on retient quand même le signal "jamais
        # connecté" plutôt que de le perdre, par sécurité (mieux vaut
        # signaler un compte qui s'avère finalement récent que d'en
        # laisser passer un vraiment jamais utilisé).
        df["is_never_used"] = never_logged_in

    # Un compte verrouillé (locked) n'est pas un compte "dormant" au sens
    # du contrôle standard ("Accounts that are in ACTIVE status but were
    # last logged in more than 90 days ago") : il est déjà bloqué, sans
    # risque d'usage immédiat, contrairement à un compte actif oublié.
    # Le mélanger aux vrais dormants diluerait la priorité réelle. Suivi
    # séparément (is_locked) plutôt qu'ignoré : un compte verrouillé
    # depuis longtemps reste un sujet de nettoyage à part entière.
    LOCKED_MARKERS = {"locked", "verrouillé", "verrouille", "bloqué", "bloque"}
    if "account_status" in df.columns:
        status_lower = df["account_status"].astype(str).str.strip().str.lower()
        df["is_locked"] = status_lower.apply(
            lambda s: any(marker in s for marker in LOCKED_MARKERS)
        )
        is_active_status = df["account_status"].apply(_is_active_account)
        df["is_dormant"] = df["is_dormant"] & is_active_status
        df["is_never_used"] = df["is_never_used"] & is_active_status
    else:
        df["is_locked"] = False

    if "account_status" in df.columns and "employee_status" in df.columns:
        df["is_terminated_but_active"] = df.apply(
            lambda r: _is_active_account(r["account_status"])
            and _is_terminated_employee(r["employee_status"]),
            axis=1,
        )
    else:
        logger.warning(
            "Colonnes 'account_status' et/ou 'employee_status' absentes : "
            "détection des comptes orphelins post-départ désactivée."
        )
        df["is_terminated_but_active"] = False

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
    PRIVILEGED_ROLE_KEYWORDS = [
        "admin", "administrator", "administrateur", "root", "superuser",
        "super user", "superadmin", "super admin", "sysadmin",
    ]
    _PRIVILEGED_ROLE_RE = re.compile(
        r"\b(" + "|".join(re.escape(k) for k in PRIVILEGED_ROLE_KEYWORDS) + r")\b"
    )
    privileged_from_flag = pd.Series(False, index=df.index)
    if "is_privileged" in df.columns:
        privileged_from_flag = df["is_privileged"].apply(_is_privileged)

    privileged_from_role = pd.Series(False, index=df.index)
    if "role" in df.columns:
        role_lower = df["role"].astype(str).str.strip().str.lower()
        privileged_from_role = role_lower.apply(
            lambda r: bool(_PRIVILEGED_ROLE_RE.search(r))
        )

    df["is_privileged_flag"] = privileged_from_flag | privileged_from_role

    if "manager" in df.columns:
        df["has_no_manager"] = df["manager"].isna() | (df["manager"].astype(str).str.strip() == "")
    else:
        df["has_no_manager"] = False

    if "password_last_set" in df.columns:
        _dayfirst_pwd = _detect_dayfirst(df["password_last_set"])
        _yearfirst_pwd = _detect_yearfirst(df["password_last_set"])
        df["days_since_password_change"] = df["password_last_set"].apply(
            lambda v: _days_since(v, dayfirst=_dayfirst_pwd, yearfirst=_yearfirst_pwd)
        )
    else:
        logger.warning("Colonne 'password_last_set' absente : détection de mot de passe périmé désactivée.")
        df["days_since_password_change"] = None

    df["is_password_stale"] = df["days_since_password_change"].apply(
        lambda d: d is not None and d > password_stale_threshold_days
    )

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
        # Contrôle 4 (Test Accounts) : indice par convention de nommage
        # uniquement — jamais traité comme une certitude (voir _TEST_ACCOUNT_RE).
        df["is_test_account"] = df["username"].apply(_is_test_account_name)
    else:
        df["is_service_account"] = False
        df["is_test_account"] = False

    # Contrôle 9 (Naming convention) : vérifiable seulement si un nom
    # complet (ou prénom/nom séparés) est disponible pour comparer à la
    # règle attendue. Résultat "non vérifiable" traité comme conforme
    # (False) pour ne pas fabriquer de faux signal sans information.
    if "username" in df.columns and ("full_name" in df.columns or ("first_name" in df.columns and "last_name" in df.columns)):
        naming_result = df.apply(_check_naming_convention, axis=1)
        df["is_non_compliant_naming"] = naming_result.fillna(False)
    else:
        df["is_non_compliant_naming"] = False

    # Comptes en doublon : la même personne détient plusieurs comptes actifs
    # pour un même usage. On approxime via le nom complet (à défaut d'un
    # identifiant employé fiable et systématiquement présent) : si un même
    # nom complet est associé à plusieurs comptes actifs sur un même
    # système, c'est un doublon à signaler.
    if "full_name" in df.columns and "account_status" in df.columns and "system" in df.columns:
        active_mask = df["account_status"].apply(_is_active_account)
        dup_counts = (
            df[active_mask]
            .groupby(["full_name", "system"])["username"]
            .transform("nunique")
        )
        df["is_duplicate_account"] = False
        df.loc[active_mask, "is_duplicate_account"] = dup_counts.reindex(df.index[active_mask]).fillna(0) > 1
    else:
        df["is_duplicate_account"] = False

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
        reasons.append(("Compte dormant ou jamais utilisé", 20))
    if row["is_privileged_flag"]:
        score += 30
        reasons.append(("Compte privilégié", 30))
    if row["is_privileged_flag"] and row.get("has_non_expiring_password", False):
        score += 25
        reasons.append(("Mot de passe n'expirant jamais (privilégié)", 25))
    if row["is_password_stale"] and not row.get("is_service_account", False):
        score += 20
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
