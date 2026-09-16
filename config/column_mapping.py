"""
Configuration du mapping de colonnes pour les exports d'accès / comptes.

Même principe que sur le projet de gestion des vulnérabilités : chaque champ
standard est associé à ses variantes possibles, pour absorber automatiquement
les différences de format entre systèmes (Active Directory, Azure AD, Okta,
exports RH, ServiceNow IGA, etc.).
"""

COLUMN_MAPPING = {
    "username": [
        "username", "login", "identifiant", "compte", "account", "user",
        "sam_account_name", "user_principal_name", "upn",
        "nom d'utilisateur", "nom utilisateur", "identifiant utilisateur",
        "samaccountname", "uid",  # attributs LDAP/AD (LDIF)
        "sam account name", "logon name", "user logon name",  # variantes espacées (exports AD)
        "userprincipalname",  # variante sans espace (export AD/Azure hybride)
        "user name",  # variante espacée très courante (ex. exports Windows/IAM génériques)
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
        "displayname", "sn", "cn",  # LDAP
        "display name",  # variante espacée (export AD)
    ],
    "first_name": [
        # Séparé de 'full_name' : quand un export ne fournit que prénom/nom
        # séparément (pas de colonne "Display Name"/"Full Name" unique), les
        # deux sont recombinés automatiquement en full_name à l'ingestion
        # (voir _synthesize_full_name dans ingestion/ingest.py).
        "first_name", "prenom", "prénom", "first name", "given name",
        "givenname",  # LDAP/IAM (WSO2 notamment) — corrige un vrai bug :
        # listé par erreur comme variante de full_name ("prénom" seul y
        # était fusionné à tort avec le nom complet, écrasant celui-ci).
    ],
    "last_name": [
        "last_name", "nom_famille", "last name", "surname", "family name",
    ],
    "email": [
        "email", "e-mail", "mail", "adresse_email", "adresse mail",
        "email address",  # variante espacée
        "emailaddress",  # variante sans espace/underscore (export IAM type WSO2)
    ],
    "phone": [
        "phone", "telephone", "téléphone", "mobile", "phone number", "numero de telephone",
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
        "linemanageremail",  # export IAM type WSO2 (adresse mail du n+1,
        # pas un nom, mais reste le bon signal "qui est le responsable")
    ],
    "system": [
        "system", "application", "systeme", "app", "target_system",
        "resource", "ressource",
    ],
    "role": [
        "role", "permission", "access_level", "niveau_acces", "droit",
        "droits", "group", "groupe", "profil",
        "memberof",  # LDAP : groupes d'appartenance
        "assigned user roles", "user roles", "assigned roles",  # variantes espacées
    ],
    "description": [
        "description", "job_description", "job description", "account_description",
        "account description", "notes", "commentaire", "commentaires",
    ],
    "account_status": [
        "account_status", "status", "statut", "etat_compte", "compte_status",
        "account_enabled", "statut_compte", "statut compte", "etat du compte",
        "useraccountcontrol",  # LDAP (décodé au parsing LDIF, voir ingestion)
        "accountstatus",  # variante sans espace
        "identity accountstate",  # export IAM type WSO2 ('identity/accountState')
    ],
    "is_privileged": [
        "is_privileged", "privileged", "admin", "is_admin", "compte_privilegie",
        "acces_privilegie", "sudo privileges", "sudo", "root access",  # exports serveurs Linux/Unix
    ],
    "last_login_date": [
        "last_login_date", "last_login", "derniere_connexion",
        "date_derniere_connexion", "last_logon",
        "lastlogontimestamp", "whenchanged",  # LDAP
        "lastlogondate",  # variante sans espace
        "when changed",  # variante espacée — même écart fuzzy que "when created"
        "identity lastlogintime",  # export IAM type WSO2 ('identity/lastLoginTime') —
        # critique : sans cette variante, la détection de dormance est
        # silencieusement désactivée sur ce type d'export.
    ],
    "account_created_date": [
        "account_created_date", "date_creation", "created_date", "creation_date",
        "date_creation_compte",
        "whencreated",  # LDAP
        "when created",  # variante espacée (export AD) — score fuzzy insuffisant sans elle
        "created",  # mot seul (export SIEM/base de données)
    ],
    "account_expiry_date": [
        "account_expiry_date", "account expiry date", "account expiry time",
        "expiration_compte", "date expiration compte",
        "accountexpirationdate",  # variante sans espace ("Expiration" plutôt que "Expiry")
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
        "last password reset date", "password reset date",  # variantes espacées
        "passwordlastset",  # variante sans espace
        "last password change date", "password change date", "last password change",
        "identity lastpasswordupdatetime",  # export IAM type WSO2
        # ('identity/lastPasswordUpdateTime') — critique : sans cette
        # variante, le contrôle d'âge des mots de passe est
        # silencieusement désactivé sur ce type d'export.
    ],
    "password_expiry_date": [
        "password_expiry_date", "password expiry date", "expiration_mdp",
        "date expiration mot de passe",
    ],
    "password_status": [
        "password_status", "password status", "statut_mdp", "statut mot de passe",
    ],
    # --- Champs informatifs : ne participent à aucune détection automatique,
    # mais sont conservés et affichés tels quels — utile quand la source
    # fournit déjà sa propre analyse ou annotation, à comparer avec la nôtre
    # plutôt qu'à écraser. ---
    "source_recommended_action": [
        "recommendedaction", "recommended_action", "recommended action",
    ],
    "source_reason": [
        "reason", "raison", "justification",
    ],
    "owner_comment": [
        "owner_comment", "owner comment", "commentaire", "comment",
    ],
}

# Champs strictement indispensables pour lancer une analyse.
# Volontairement minimal : les autres champs enrichissent l'analyse mais
# ne sont pas bloquants s'ils sont absents.
REQUIRED_FIELDS = ["username", "system"]
