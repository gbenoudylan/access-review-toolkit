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


def _normalize(text) -> str:
    if text is None or (isinstance(text, float) and pd.isna(text)):
        return ""
    return str(text).strip().lower()


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
    # peut apparaître sur plusieurs lignes, une par système/rôle)
    roles_per_user: dict[str, set[str]] = {}
    for _, row in df.iterrows():
        user = row["username"]
        raw_roles = str(row["role"]) if pd.notna(row["role"]) else ""
        for r in raw_roles.replace(";", ",").split(","):
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

    df["sod_conflict"] = df["username"].map(lambda u: u in conflict_by_user)
    df["sod_conflict_detail"] = df["username"].map(lambda u: conflict_by_user.get(u, ""))

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
