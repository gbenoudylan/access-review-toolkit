"""
Reproduction fidèle des sections I (Objectif) et II (Principes de création
de compte) du template officiel de revue de comptes applicatifs, jusqu'à
la section "Account Creation Process" incluse.

Ce module est volontairement isolé du reste de la logique de génération
(reporting/export.py) : ce texte est une reproduction fidèle demandée par
l'entreprise pour produire un document officiel dans un dépôt de code
désormais privé — il ne doit jamais être publié dans un dépôt public.
"""

# Les 18 contrôles, texte fidèle du template (SN, Control, Description/Expectations)
TEMPLATE_CONTROLS = [
    (1, "Dump completeness and accuracy",
     "Extraction with relevant field and update that help reviewer to perform objective review/ "
     "Verify that the user access dump provided by the extractor is complete, not modified, and "
     "contains all required attributes (User ID, creation date , User rights , Password reset "
     "date,Last login, Account status ,and description)"),
    (2, "Dormant Accounts",
     "Accounts that are in active status but were last logged in more than 90 days ago/such "
     "account need to be disabled. Normal user account disabled for 60 days should be deleted. "
     "Admin account must be disabled but not deleted.System account must not be deleted unless "
     "authorized by the system owner."),
    (3, "Orphaned accounts",
     "Accounts with active status, but no information that would allow the holder to be "
     "positively identified (MTMB employee, support, service, etc.)/These accounts must be "
     "renamed when possible ."),
    (4, "Test Accounts",
     "Accounts created for testing purposes/These accounts must be disabled just after the test "
     "on production systems"),
    (5, "Active accounts",
     "User accounts in an \"Active\" status whose last login date is included in the time period "
     "covered by the current report. /These accounts must be created when form is duly signed "
     "and service now ticket has been created"),
    (6, "Inactive accounts",
     "Accounts created but never used/logged in, more than 30 days after creation./These accounts "
     "must be disable following the internal process"),
    (7, "Service Accounts",
     "Accounts used by automated processes or third-party applications.Normally, the name of a "
     "service account is preceded or ended by \"*_SVC or SVC_*\"./These accounts must be approved "
     "and include in the inventory service account.Note: They must be on an up-to-date list of "
     "service accounts validated by the CTIO or DM.\nEx:svc_ISE\nISE_svc"),
    (8, "Duplicate accounts",
     "Several active accounts are held by the same natural person and used for the same purpose "
     "(same profile or same privileges)./All of these duplicate account must be disabled to leave "
     "only one active account."),
    (9, "Active Non-compliant logins",
     "Active accounts that do not follow the MTN LIBERIA naming convention, /These account must "
     "be identified and renamed using the naming convention if possible\nNaming convention:\nThe "
     "user ID format consists of the first letter of the user's first name followed by the "
     "user's last name.\nExample: Michael Brown → mbrown"),
    (10, "Accounts created",
     "Accounts created since the previous review./These accounts must be approved before "
     "creation and approval must be rigorously kept for auditing purpose.The service now ID of "
     "that account must be kept for auditing purpose as well."),
    (11, "Profile Modified",
     "Accounts where the profile has changed since the previous review./Ensure all profile "
     "modification has been properly approved."),
    (12, "Reactivated accounts",
     "Accounts reactivated since the previous review/Review all accounts reactivated since the "
     "previous review period. Confirm that valid justification and appropriate approvals exist "
     "before access restoration."),
    (13, "Deleted accounts",
     "Accounts deleted since the previous review/Ensure all deletion are done according the "
     "process(Keep the justification)."),
    (14, "Password Ages",
     "Password ages, calculated since the last modification, to detect accounts with a password "
     "age greater than 90 days./Except service account ,All accounts that the age exceed 90 days "
     "must be changed or disable.Any account that is exceptional must be recorded for "
     "documentation."),
    (15, "3PP (Third-Party Personnel)",
     "Owner must identify the account belong to contractors, consultants, vendors, or external "
     "parties and confirm active contracts between MTN and third party and all active users are "
     "still part of third party personnel/\nOwner must have the list of all Third-Party Personnel "
     "and confirm that all thirst party personnel still working with MTN and have contractual "
     "evidence update for the audit purpose."),
    (16, "Administrator Accounts",
     "Account with highest privilege /These accounts must be approved, reviewed and be maintained"),
    (17, "Annual User Profile and matrix review",
     "Application owner must perform the review of user profile and application matrix review at "
     "least annually/ Owner has performed the review of the profile and matrix of application "
     "last year and has the review report signed."),
    (18, "Terminated Users and Transferred users",
     "User(MTNER or third party ) who left MTN or User who changed the position have they access "
     "revoked /Verify that accounts belonging to terminated employees, contractors, or third "
     "parties have been disabled as defined by the User Access Management Procedure."),
]

OBJECTIVE_INTRO = (
    "The objective of this review is to ensure that all accounts created meet the established "
    "security criteria and that:"
)
OBJECTIVE_BULLETS = [
    "There are no undocumented or unauthorized accounts.",
    "Privileges are in line with needs.",
    "Inactive or orphaned accounts are identified and deactivated.",
    "Access is properly restricted to limit the risk of compromise.",
]
OBJECTIVE_CONTROL_INTRO = "Below is the control definition:"

PRINCIPLES_INTRO = (
    "When setting up a new server or infrastructure, it is essential to follow strict principles "
    "for the creation of user accounts. These principles aim to ensure that only legitimate "
    "users, with the necessary rights, can access resources. The purpose of the review of the "
    "accounts is to ensure that these principles are respected."
)

ACCOUNT_TYPES = [
    ("Standard User Accounts:", " Created for employees or end users, these accounts have "
     "limited privileges, corresponding to the needs of their functions."),
    ("Administrator Accounts:", " These accounts are reserved for system administrators and "
     "have broader access for managing servers and applications."),
    ("Service Accounts:", " Used to run specific applications or services on the server, these "
     "accounts do not require interactive access and must be configured with limited privileges."),
]

CREATION_PROCESS_INTRO = "Account creation follows a formalized process that includes:"
CREATION_PROCESS_ITEMS = [
    ("Documented Request:", " Each account creation must be justified by a formal request, "
     "validated by minimum the user manager and data owner."),
    ("Definition of Privileges:", " When creating, privileges must be assigned and respected"),
]

# ---------------------------------------------------------------------
# IV. ACCOUNT DETAILS BY CONTROL — texte fidèle du template : 18 sous-
# sections numérotées, chacune avec son propre titre exact et, quand le
# template en fournit une, sa consigne ("Guidance"). Champ 'key' relie
# chaque sous-section à ce que l'outil sait effectivement calculer
# (voir reporting/export.py) ; None quand la donnée n'est pas calculable
# automatiquement sans configuration propre à l'entreprise.
SECTION_IV_INTRO = "This section presents detailed information on the accounts reviewed under each control performed"

DUMP_COMPLETENESS_HEADER = "1.Dump completeness and accuracy"
DUMP_COMPLETENESS_GUIDANCE = "Fill the table with OK or NOK"
# Libellés de colonnes fidèles au template (pas ceux, reformulés, du
# tableau générique construit précédemment).
DUMP_COMPLETENESS_COLUMNS = [
    ("User logon (User ID)", ["username", "user_id"]),
    ("User creation DATE", ["account_created_date"]),
    ("User rights or permissions", ["role"]),
    ("Password reset date", ["password_last_set"]),
    ("Last login date", ["last_login_date"]),
    ("Account status", ["account_status"]),
]

# (numéro, titre exact, consigne exacte ou None, clé de donnée ou None)
CONTROL_SUBSECTIONS = [
    (2, "Dormant Accounts", "Guidance : Check the last login that exceed 90 days", "is_dormant"),
    (3, "Orphaned Accounts", None, None),
    (4, "Test Accounts", None, None),
    (5, "Active Accounts", None, "_active_count"),
    (6, "Inactive Accounts", None, None),
    (7, "Service Accounts", None, "is_service_account"),
    (8, "Duplicate Accounts", None, "is_duplicate_account"),
    (9, "Active Non-compliant logins", None, None),
    (10, "Accounts created",
     "Guidance: check the creation date of the extraction to identify new account , if the "
     "system does not provide creation , perform the comparison between the last extraction "
     "and the extraction to identify new account.Once new account is identified , check if "
     "that account has been approved and that account is associated with service now ID.",
     "_created"),
    (11, "Profile Modified", None, "_profile_modified"),
    (12, "Reactivated accounts", None, "_reactivated"),
    (13, "Deleted accounts", None, "_deleted"),
    (14, "Password ages<=90days", None, "is_password_stale"),
    (15, "3PP Accounts", None, None),
    (16, "Administrator Accounts", None, "is_privileged_flag"),
    (17, "Annual User Profile and matrix review", None, None),
    (18, "Terminated Users and Transferred users",
     "Guidance : Get the list of terminated staff and the list of people that have changed "
     "position from HR and get the list of relevant contractor that have access to the "
     "systems , compare that list of active user in the application.",
     "is_terminated_but_active"),
]

CONCLUSION_HEADING = "V. CONCLUSION"

