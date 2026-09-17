"""
Module de détection de conflits de séparation des tâches (Segregation of
Duties, SoD).

Principe : certaines combinaisons de rôles/permissions ne doivent jamais
être cumulées par la même personne, car cela permettrait à un individu
seul de contourner un contrôle censé nécessiter deux personnes distinctes
(ex. créer un paiement ET le valider). C'est un contrôle standard en audit
interne et en conformité financière (LCB-FT/AMLD, SOX...).

La matrice de conflits ci-dessous est un point de départ générique,
inspirée des conflits SoD les plus classiques en entreprise (finance,
achats, IT). Elle est faite pour être adaptée : chaque organisation a sa
propre matrice de rôles incompatibles.
"""

from __future__ import annotations
import logging

import pandas as pd

from analysis.hr_crossref import _normalize_name_bag

logger = logging.getLogger("sod")

# Matrice de conflits : chaque paire de rôles listée ne doit jamais être
# détenue simultanément par la même personne. La comparaison se fait sur
# une version normalisée (minuscules) du contenu du champ 'role'.
DEFAULT_SOD_CONFLICTS = [
    ("créer paiement", "valider paiement"),
    ("create payment", "approve payment"),
    ("créer fournisseur", "valider paiement"),
    ("create vendor", "approve payment"),
    ("créer commande", "valider commande"),
    ("create order", "approve order"),
    ("admin système", "auditeur"),
    ("system admin", "auditor"),
    ("gestion des accès", "revue des accès"),
    ("access management", "access review"),
    ("développeur", "déploiement production"),
    ("developer", "production deploy"),
]


def load_custom_sod_matrix(file_bytes: bytes, filename: str) -> list[tuple[str, str]]:
    """
    Charge une matrice de conflits SoD personnalisée depuis un fichier
    Excel/CSV à deux colonnes (peu importe leur nom exact — les deux
    premières colonnes sont utilisées), une paire de rôles incompatibles
    par ligne. Permet à chaque entreprise d'adapter la matrice sans
    modifier le code : chaque organisation a sa propre liste de rôles
    incompatibles, la matrice par défaut n'est qu'un point de départ.

    Une paire où les deux rôles sont identiques (ex. 'Admin'/'Admin' —
    une erreur de saisie plausible dans un tableur maintenu à la main)
    est ignorée : un rôle ne peut pas être en conflit avec lui-même, et
    la garder ferait signaler à tort TOUT compte portant simplement ce
    rôle une seule fois, une explosion de faux positifs pour une simple
    coquille dans la matrice source.
    """
    import io
    from pathlib import Path as _Path
    suffix = _Path(filename).suffix.lower()
    if suffix == ".csv":
        matrix_df = pd.read_csv(io.BytesIO(file_bytes))
    else:
        matrix_df = pd.read_excel(io.BytesIO(file_bytes))
    if matrix_df.shape[1] < 2:
        raise ValueError("La matrice SoD doit contenir au moins deux colonnes (rôle 1, rôle 2).")
    col_a, col_b = matrix_df.columns[:2]
    pairs = []
    self_referential_count = 0
    for _, row in matrix_df.iterrows():
        a, b = row[col_a], row[col_b]
        if pd.notna(a) and pd.notna(b) and str(a).strip() and str(b).strip():
            a_clean, b_clean = str(a).strip(), str(b).strip()
            if a_clean.lower() == b_clean.lower():
                self_referential_count += 1
                continue
            pairs.append((a_clean, b_clean))
    if self_referential_count:
        logger.warning(
            f"{self_referential_count} ligne(s) de la matrice SoD ignorée(s) car les deux "
            f"rôles y sont identiques (probable erreur de saisie) — un rôle ne peut pas "
            f"être en conflit avec lui-même."
        )
    return pairs


def _normalize(text) -> str:
    if text is None or (isinstance(text, float) and pd.isna(text)):
        return ""
    return str(text).strip().lower()


def _split_roles(raw_roles: str) -> list[str]:
    """
    Découpe un champ 'role' multi-valué en rôles individuels.

    Priorité, du séparateur le plus sûr au moins sûr :
    1. Retour à la ligne — un nom de rôle métier ne contient
       pratiquement jamais de saut de ligne littéral, contrairement à
       une virgule ('Manager, Finance Department') ; c'est le motif
       rencontré dans un vrai export (Oracle EBS, MTN) où une cellule
       Excel liste un rôle par ligne (Alt+Entrée) pour un même compte —
       sans ce découpage, tous les rôles d'un utilisateur fusionnaient
       en une seule chaîne informe, désactivant silencieusement toute
       détection de conflit SoD pour ce fichier.
    2. Point-virgule — si présent (et pas de retour à la ligne), il
       prime sur la virgule : un nom de rôle métier peut légitimement
       contenir une virgule ('Manager, Finance Department', 'Analyst,
       Level 2') — convertir systématiquement ';' en ',' avant de
       découper détruirait cette distinction et couperait un rôle
       UNIQUE en deux fragments, pouvant déclencher un faux conflit SoD
       si ces fragments correspondaient par coïncidence à une paire de
       la matrice.
    3. Virgule — seul séparateur restant si aucun des deux précédents
       n'est présent.
    """
    if "\n" in raw_roles:
        return raw_roles.split("\n")
    if ";" in raw_roles:
        return raw_roles.split(";")
    return raw_roles.split(",")


def detect_sod_conflicts(
    df: pd.DataFrame, conflicts: list[tuple[str, str]] = None
) -> pd.DataFrame:
    """
    Détecte, pour chaque utilisateur, s'il cumule des rôles en conflit.

    Fonctionne à partir de la colonne 'role', qui peut contenir plusieurs
    rôles séparés par une virgule ou un point-virgule pour un même compte
    (cas fréquent : un utilisateur avec plusieurs accès sur un même
    système, ou plusieurs lignes par utilisateur dans l'export source).

    Ajoute deux colonnes :
        - sod_conflict : True si un conflit est détecté
        - sod_conflict_detail : description du conflit trouvé (ou vide)
    """
    conflicts = conflicts or DEFAULT_SOD_CONFLICTS
    df = df.copy()

    if "role" not in df.columns:
        logger.warning("Colonne 'role' absente : détection SoD désactivée.")
        df["sod_conflict"] = False
        df["sod_conflict_detail"] = ""
        return df

    if "username" not in df.columns:
        logger.warning("Colonne 'username' absente : détection SoD désactivée.")
        df["sod_conflict"] = False
        df["sod_conflict_detail"] = ""
        return df

    # Regroupe tous les rôles détenus par chaque utilisateur (un utilisateur
    # peut apparaître sur plusieurs lignes, une par système/rôle).
    # Clé NORMALISÉE (espaces/casse) : la même personne peut apparaître avec
    # une casse différente selon le système source (ex. 'jdupont' sur AD,
    # 'JDupont' sur SAP) — sans cette normalisation, un conflit SoD réparti
    # sur plusieurs systèmes (ex. créateur de paiement sur l'un, validateur
    # sur l'autre) serait scindé en deux identités distinctes et jamais
    # détecté, alors que c'est exactement le genre de conflit que ce
    # contrôle doit attraper.
    def _norm_user(u):
        return str(u).strip().lower()

    roles_per_user: dict[str, set[str]] = {}
    for _, row in df.iterrows():
        user = _norm_user(row["username"])
        raw_roles = str(row["role"]) if pd.notna(row["role"]) else ""
        for r in _split_roles(raw_roles):
            r_norm = _normalize(r)
            if r_norm:
                roles_per_user.setdefault(user, set()).add(r_norm)

    conflict_by_user: dict[str, str] = {}
    for user, roles in roles_per_user.items():
        for role_a, role_b in conflicts:
            role_a_norm, role_b_norm = _normalize(role_a), _normalize(role_b)
            has_a = any(role_a_norm in r for r in roles)
            has_b = any(role_b_norm in r for r in roles)
            if has_a and has_b:
                conflict_by_user[user] = f"{role_a} + {role_b}"
                break  # un conflit détecté suffit à flaguer l'utilisateur

    df["sod_conflict"] = df["username"].map(lambda u: _norm_user(u) in conflict_by_user)
    df["sod_conflict_detail"] = df["username"].map(lambda u: conflict_by_user.get(_norm_user(u), ""))

    # Second passage, par NOM COMPLET : un identifiant technique diffère
    # très souvent d'un système à l'autre (chaque système génère son
    # propre identifiant de connexion — 'jdupont' sur AD, 'jean.dupont'
    # sur SAP, un matricule ailleurs) — la simple normalisation de casse
    # ci-dessus ne suffit alors pas à relier les deux comptes de la même
    # personne, et un conflit réparti sur ces systèmes resterait invisible.
    # Le nom complet reste souvent la seule donnée commune aux deux
    # systèmes. Volontairement séparé du passage par identifiant : deux
    # personnes DIFFÉRENTES peuvent porter le même nom (homonymes), donc
    # ce repli est un signal de MOINDRE confiance, marqué comme tel dans
    # le détail plutôt que mélangé aux conflits confirmés par identifiant.
    if "full_name" in df.columns:
        roles_per_name: dict[tuple, set[str]] = {}
        for _, row in df.iterrows():
            name_key = _normalize_name_bag(row.get("full_name"))
            if not name_key:
                continue
            raw_roles = str(row["role"]) if pd.notna(row["role"]) else ""
            for r in _split_roles(raw_roles):
                r_norm = _normalize(r)
                if r_norm:
                    roles_per_name.setdefault(name_key, set()).add(r_norm)

        conflict_by_name: dict[tuple, str] = {}
        for name_key, roles in roles_per_name.items():
            for role_a, role_b in conflicts:
                role_a_norm, role_b_norm = _normalize(role_a), _normalize(role_b)
                has_a = any(role_a_norm in r for r in roles)
                has_b = any(role_b_norm in r for r in roles)
                if has_a and has_b:
                    conflict_by_name[name_key] = f"{role_a} + {role_b}"
                    break

        if conflict_by_name:
            name_keys = df["full_name"].apply(_normalize_name_bag)
            already_flagged = df["sod_conflict"]
            name_conflict_mask = name_keys.isin(conflict_by_name) & ~already_flagged
            if name_conflict_mask.any():
                logger.warning(
                    f"{int(name_conflict_mask.sum())} compte(s) signalé(s) en conflit SoD "
                    "uniquement par rapprochement de NOM (identifiant technique différent "
                    "d'un système à l'autre) — confiance moindre qu'un conflit confirmé par "
                    "identifiant : à vérifier qu'il ne s'agit pas de deux personnes "
                    "homonymes distinctes."
                )
            df.loc[name_conflict_mask, "sod_conflict"] = True
            df.loc[name_conflict_mask, "sod_conflict_detail"] = name_keys[name_conflict_mask].map(
                lambda k: f"{conflict_by_name[k]} (rapproché par nom, PAS par identifiant "
                          f"— à vérifier : pourrait être deux personnes homonymes distinctes "
                          f"plutôt qu'un vrai conflit)"
            )

    n_conflicts = len(conflict_by_user)
    if n_conflicts:
        logger.info(f"{n_conflicts} utilisateur(s) avec un conflit SoD détecté.")
    else:
        logger.info("Aucun conflit SoD détecté.")

    return df


if __name__ == "__main__":
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent.parent))

    logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")

    from ingestion.ingest import load_file

    if len(sys.argv) < 2:
        print("Usage : python -m analysis.sod_detection <fichier>")
        sys.exit(1)

    df_in = load_file(sys.argv[1])
    df_out = detect_sod_conflicts(df_in)
    conflicts_only = df_out[df_out["sod_conflict"]]
    print(conflicts_only[["username", "role", "sod_conflict_detail"]].drop_duplicates())
