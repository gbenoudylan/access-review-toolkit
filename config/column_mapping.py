"""
Configuration du mapping de colonnes pour les exports d'accès / comptes.

Même principe que sur le projet de gestion des vulnérabilités : chaque champ
standard est associé à ses variantes possibles, pour absorber automatiquement
les différences de format entre systèmes (Active Directory, Azure AD, Okta,
exports RH, ServiceNow IGA, etc.).
"""

COLUMN_MAPPING = {
    "username": [
        "username", "login", "identifiant", "compte", "account",
        "sam_account_name", "user_principal_name", "upn",
        "nom d'utilisateur", "nom utilisateur", "identifiant utilisateur",
        "samaccountname", "uid",  # attributs LDAP/AD (LDIF)
        "sam account name", "logon name", "user logon name",  # variantes espacées (exports AD)
    ],
    "user_id": [
        # Distinct du nom de connexion : souvent un identifiant employé/
        # matricule interne (numérique ou non), utilisé pour le
        # rapprochement avec les systèmes RH plutôt que pour se connecter.
        "user_id", "employee_id", "matricule", "id_employe", "staff_id",
        "badge_number", "numero_employe", "employee number", "staff id",
        "id employe", "matricule employe",
    ],
    "full_name": [
        "full_name", "fullname", "nom_complet", "nom", "name", "display_name",
        "nom prenom", "employee_name",
        "displayname", "givenname", "sn", "cn",  # LDAP
        "display name",  # variante espacée (export AD)
    ],
    "email": [
        "email", "e-mail", "mail", "adresse_email", "adresse mail",
    ],
    "department": [
        "department", "departement", "département", "service", "direction",
        "business_unit", "bu",
        "departmentnumber", "ou",  # LDAP
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
        "memberof",  # LDAP : groupes d'appartenance
    ],
    "account_status": [
        "account_status", "status", "statut", "etat_compte", "compte_status",
        "account_enabled", "statut_compte", "statut compte", "etat du compte",
        "useraccountcontrol",  # LDAP (décodé au parsing LDIF, voir ingestion)
    ],
    "is_privileged": [
        "is_privileged", "privileged", "admin", "is_admin", "compte_privilegie",
        "acces_privilegie",
    ],
    "last_login_date": [
        "last_login_date", "last_login", "derniere_connexion",
        "date_derniere_connexion", "last_logon",
        "lastlogontimestamp", "whenchanged",  # LDAP
    ],
    "account_created_date": [
        "account_created_date", "date_creation", "created_date", "creation_date",
        "date_creation_compte",
        "whencreated",  # LDAP
        "when created",  # variante espacée (export AD) — score fuzzy insuffisant sans elle
    ],
    "employee_status": [
        "employee_status", "statut_employe", "hr_status", "statut_rh",
        "employment_status",
    ],
    # --- Hygiène des mots de passe : absent du référentiel jusqu'ici, alors
    # que c'est un axe de revue d'accès aussi standard que la dormance de
    # connexion (ex. colonnes "Password Last Set", "Password Expiry Date"
    # d'un export Active Directory classique). ---
    "password_last_set": [
        "password_last_set", "password last set", "derniere_modif_mdp",
        "dernier changement mot de passe", "pwdlastset",
    ],
    "password_expiry_date": [
        "password_expiry_date", "password expiry date", "expiration_mdp",
        "date expiration mot de passe",
    ],
    "account_expiry_date": [
        "account_expiry_date", "account expiry date", "account expiry time",
        "expiration_compte", "date expiration compte",
    ],
    "password_status": [
        "password_status", "password status", "statut_mdp", "statut mot de passe",
    ],
}

# Champs strictement indispensables pour lancer une analyse.
# Volontairement minimal : les autres champs enrichissent l'analyse mais
# ne sont pas bloquants s'ils sont absents.
REQUIRED_FIELDS = ["username", "system"]
