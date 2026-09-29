"""
Configuration du mapping de colonnes pour les exports d'accès / comptes.

Même principe que sur le projet de gestion des vulnérabilités : chaque champ
standard est associé à ses variantes possibles, pour absorber automatiquement
les différences de format entre systèmes (Active Directory, Azure AD, Okta,
exports RH, ServiceNow IGA, etc.).
"""

COLUMN_MAPPING = {
    "username": [
        "user",  # BSS Q3 format
        "username", "login", "identifiant", "compte", "account", "user",
        "sam_account_name", "user_principal_name", "upn",
        "nom d'utilisateur", "nom utilisateur", "identifiant utilisateur",
        "samaccountname", "uid",  # attributs LDAP/AD (LDIF)
        "sam account name", "logon name", "user logon name",  # variantes espacées (exports AD)
        "samaccount",  # export Oracle Identity Manager (colonne "SAMACCOUNT", sans le suffixe "NAME")
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
        "samaccount2",  # export Oracle Identity Manager — second identifiant,
        # observé porteur du nom affiché (qualité variable selon les comptes).
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
        "hostname", "host_name", "host",  # BSS Q3 format
        "system", "application", "systeme", "app", "target_system",
        "resource", "ressource",
    ],
    "role": [
        # Champ "rôle" : profil métier, fonction, poste dans le système.
        # Noms de colonnes qui décrivent CE QUE L'UTILISATEUR EST (son rôle).
        "role", "roles", "profile", "profil", "profils",
        "user profile", "user type", "account type", "account role",
        "function", "functions", "group", "groupe",
        "memberof",  # LDAP
        "assigned user roles", "user roles", "assigned roles",
        "security role", "system role", "application roles",
        "responsibility", "responsibilities",
    ],
    "user_rights": [
        # Champ "droits / permissions" : ce que l'utilisateur PEUT FAIRE —
        # les permissions, droits d'accès, entitlements réellement octroyés.
        # Distinct de "role" : dans Oracle EBS, USER RIGHTS/PERMISSIONS liste
        # les modules et actions accessibles, indépendamment du rôle métier.
        "user rights/permissions", "user rights permissions",
        "user rights", "user rights and privileges", "user rights & privileges",
        "permissions", "permission", "rights", "droits", "droit",
        "access rights", "access level", "access_level", "niveau_acces",
        "entitlement", "entitlements",
        "privilege", "privileges",
        "authority", "authorization", "authorizations",
        "application access", "user access",
        "assigned access", "access granted",
    ],
    "days_since_last_login_precomputed": [
        "days since last login", "days since last login ", "days_since_last_login",
        "days since last logon", "days inactive", "inactivity days",
    ],
    "description": [
        "description", "job_description", "job description", "account_description",
        "account description", "account_comment", "account comment",
        "notes", "note", "commentaire", "commentaires", "comment", "comments",
        "remarks", "remark", "info", "information",
        # Variantes fréquentes dans les exports AD/LDAP
        "displayname", "display name", "full description", "user description",
    ],
    "account_status": [
        "account_status", "status", "statut", "etat_compte", "compte_status",
        "account_enabled", "statut_compte", "statut compte", "etat du compte",
        "useraccountcontrol",  # LDAP (décodé au parsing LDIF, voir ingestion)
        "obuseraccountcontrol",  # export Oracle Identity Manager — équivalent
        # fonctionnel de useraccountcontrol, mais déjà en clair ("activated"/
        # "deactivated") : pas de décodage bit à bit à appliquer ici.
        "accountstatus",  # variante sans espace
        "identity accountstate",  # export IAM type WSO2 ('identity/accountState')
        "enabled", "is_enabled",  # PKI / AD exports (True/False) — NOM de colonne uniquement
        # "active" intentionnellement absent : c'est une VALEUR de statut
        # (ex. "Active", "Inactive"), pas un nom de colonne, et l'ajouter
        # ici confond le détecteur de header sur les fichiers sans en-tête.
    ],
    "is_privileged": [
        "is_privileged", "privileged", "admin", "is_admin", "compte_privilegie",
        "acces_privilegie", "sudo privileges", "sudo", "root access",  # exports serveurs Linux/Unix
    ],
    "is_locked": [
        "pwdlock", "pwd_lock",  # BSS: 1=locked, 0=not locked
        "is_locked", "locked", "account_locked", "verrouille", "verrouillé",
        "lock_status", "lockout_status", "lockedout", "locked_out",
        "account_lockout", "is_lockedout",
    ],
    "last_login_date": [
        "lastlogin", "last login", "last_login",  # BSS format
        "last login (raw)", "last login raw", "latest login", "latest login date", "latest login time",  # GUI NMS  # export Oracle BIB/TABS/GGATE
        "last_login_date", "last_login", "derniere_connexion",
        "date_derniere_connexion", "last_logon",
        "lastlogontimestamp", "whenchanged",  # LDAP
        "lastlogondate",  # variante sans espace
        "when changed",   # variante espacée
        "identity lastlogintime",  # export IAM type WSO2 ('identity/lastLoginTime')
        "lastlogon",  # variante sans espace/underscore (export Oracle Identity Manager) —
        # seule variante ajoutée pour ce type d'export : contrairement à
        # whenChanged/modifyTimestamp (horodatage de toute modification, pas
        # forcément une connexion), LastLogon reflète une vraie activité
        # utilisateur. Mapper modifyTimestamp ici aussi créerait un conflit
        # de colonnes dupliquées où la mauvaise valeur écraserait la bonne.
        # Oracle EBS : MODIFICATION_DATE est la dernière date de modification du
        # compte (changement de rôle, réinitialisation mdp...) — utilisée comme
        # proxy de dernière activité quand last_login_date n'est pas disponible
        # dans l'export. Décision de l'auditeur : s'il ne veut pas l'utiliser,
        # il peut réassigner cette colonne à "Ignorée" dans le dashboard.
        "modification_date", "last_modified", "date_modification",
        "modified_date", "date_modif",
    ],
    "account_created_date": [
        "creation time", "created time", "creation date",  # GUI NMS / NE exports
        "user created", "user creation date", "created date",
        "account_created_date", "date_creation", "created_date", "creation_date",
        "date_creation_compte",
        "whencreated",  # LDAP
        "when created",  # variante espacée (export AD) — score fuzzy insuffisant sans elle
        "created",  # mot seul (export SIEM/base de données)
        "createtimestamp",  # attribut LDAP standard (Oracle Identity Manager, OpenLDAP...)
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
        "pwdchange", "pwd_change", "password change",  # BSS format
        "password_last_set", "password last set", "derniere_modif_mdp",
        "dernier changement mot de passe", "pwdlastset",
        "last password reset date", "password reset date",  # variantes espacées
        "passwordlastset",  # variante sans espace
        "last password change date", "password change date", "last password change",
        "identity lastpasswordupdatetime",  # export IAM type WSO2
        # ('identity/lastPasswordUpdateTime') — critique : sans cette
        # variante, le contrôle d'âge des mots de passe est
        # silencieusement désactivé sur ce type d'export.
        "pwdchangedtime",  # attribut LDAP standard (Oracle Identity Manager)
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
