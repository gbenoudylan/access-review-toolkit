"""
Configuration du mapping de colonnes pour les exports d'accès / comptes.

Même principe que sur le projet de gestion des vulnérabilités : chaque champ
standard est associé à ses variantes possibles, pour absorber automatiquement
les différences de format entre systèmes (Active Directory, Azure AD, Okta,
exports RH, ServiceNow IGA, etc.).
"""

COLUMN_MAPPING = {
    "username": [
        "username", "user_id", "login", "identifiant", "compte", "account",
        "sam_account_name", "user_principal_name", "upn",
        "nom d'utilisateur", "nom utilisateur", "identifiant utilisateur",
    ],
    "full_name": [
        "full_name", "fullname", "nom_complet", "nom", "name", "display_name",
        "nom prenom", "employee_name",
    ],
    "email": [
        "email", "e-mail", "mail", "adresse_email", "adresse mail",
    ],
    "department": [
        "department", "departement", "département", "service", "direction",
        "business_unit", "bu",
    ],
    "job_title": [
        "job_title", "poste", "fonction", "title", "intitule_poste",
    ],
    "manager": [
        "manager", "manager_name", "responsable", "n+1", "superieur",
        "reporting_manager", "owner",
    ],
    "system": [
        "system", "application", "systeme", "app", "target_system",
        "resource", "ressource",
    ],
    "role": [
        "role", "permission", "access_level", "niveau_acces", "droit",
        "droits", "group", "groupe", "profil",
    ],
    "account_status": [
        "account_status", "status", "statut", "etat_compte", "compte_status",
        "account_enabled", "statut_compte", "statut compte", "etat du compte",
    ],
    "is_privileged": [
        "is_privileged", "privileged", "admin", "is_admin", "compte_privilegie",
        "acces_privilegie",
    ],
    "last_login_date": [
        "last_login_date", "last_login", "derniere_connexion",
        "date_derniere_connexion", "last_logon",
    ],
    "account_created_date": [
        "account_created_date", "date_creation", "created_date", "creation_date",
        "date_creation_compte",
    ],
    "employee_status": [
        "employee_status", "statut_employe", "hr_status", "statut_rh",
        "employment_status",
    ],
}

# Champs strictement indispensables pour lancer une analyse.
# Volontairement minimal : les autres champs enrichissent l'analyse mais
# ne sont pas bloquants s'ils sont absents.
REQUIRED_FIELDS = ["username", "system"]
