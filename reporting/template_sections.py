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
    (14, "Expired password(Password age> 90days)",
     "Expectations: Except service accounts , all active user accounts must have a password "
     "changed within the last 90 days. Accounts with expired passwords must be flagged to the "
     "system owner for immediate reset or formal justification.\n"
     " All services accounts must be changed on annually basis.\n"
     "Note:As per policy , password of service account must be changed every 365 days"),
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
    (19, "First line user access review report and accuracy",
     "Verify that the application owner performs monthly user access reviews using the "
     "approved template and confirm that the review report is complete, accurate, and "
     "supported by sufficient evidence."),
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
DUMP_COMPLETENESS_GUIDANCE = (
    "Find the dumps accuracy and completeness result below:\n"
    "*Take action to provide the NOK field when possible.\n"
    "NB: Normally the account description should contain the service now ticket "
    "that will help to track back the account during the audit."
)
# Libellés de colonnes fidèles au template (pas ceux, reformulés, du
# tableau générique construit précédemment).
DUMP_COMPLETENESS_COLUMNS = [
    ("User logon (User ID)", ["username", "user_id"]),
    ("User creation DATE", ["account_created_date"]),
    ("User rights or permissions", ["user_rights", "role"]),  # user_rights (Oracle EBS) OU role
    ("Description", ["description"]),
    ("Password reset date", ["password_last_set"]),
    ("Last login date", ["last_login_date"]),
    ("Account status", ["account_status"]),
]

# (numéro, titre exact, consigne exacte ou None, clé de donnée ou None)
CONTROL_SUBSECTIONS = [
    (2, "Dormant Accounts",
     "Expectations: All active accounts must have been used within the last 90 days. "
     "Accounts with no login activity beyond this threshold must be disabled or justified "
     "by the system owner with a formal risk acceptance.",
     "is_dormant"),
    (3, "Orphaned accounts",
     "Expectations: Every active account must have an identified owner. "
     "Generic, shared, or unattributed accounts must be formally justified, "
     "reassigned to a named individual, or disabled.",
     "is_orphaned_account"),
    (4, "Test Accounts",
     "Expectations: Test accounts must not exist in production environments. "
     "Any test account found active in production must be immediately disabled "
     "and its creation justified by the system owner.",
     "is_test_account"),
    (5, "Active accounts",
     "Expectations: All active accounts must correspond to current employees or "
     "authorized service accounts. Each account must be reviewed and confirmed "
     "as legitimate by the system owner.",
     "_active_count"),
    (6, "Inactive accounts",
     "Expectations: Accounts that have never been used since creation beyond the "
     "defined threshold must be investigated and either activated with justification "
     "or disabled.",
     "is_never_used"),
    (7, "Service Accounts",
     "Expectations: All service accounts must be documented, have a named owner, "
     "and follow the principle of least privilege. Shared passwords must be rotated "
     "regularly and access must be limited to the required functions only.",
     "is_service_account"),
    (8, "Duplicate accounts",
     "Expectations: No user should hold more than one active account unless formally "
     "justified. Duplicate accounts increase the risk of unauthorized access and "
     "complicate audit trails.",
     "is_duplicate_account"),
    (9, "Active Non-compliant logins",
     "Expectations: All login attempts must comply with the defined security policy "
     "(password complexity, MFA where required, authorized IP ranges). "
     "Non-compliant active sessions must be investigated.",
     None),
    (10, "Accounts created",
     "Expectations: All new accounts must be created following an approved request "
     "(e.g. ServiceNow ticket). The system owner must confirm that each new account "
     "is authorized, properly configured, and assigned to a named individual.",
     "_created"),
    (11, "Profile Modified",
     "Expectations: Any change to an account profile (role, permissions, access level) "
     "must be authorized and traceable to an approved change request. "
     "Unauthorized profile changes must be reverted and investigated.",
     "_profile_modified"),
    (12, "Reactivated accounts",
     "Expectations: Reactivated accounts must correspond to an approved re-onboarding "
     "request. The system owner must confirm the reactivation is legitimate and "
     "that access rights remain appropriate.",
     "_reactivated"),
    (13, "Deleted accounts",
     "Expectations: All account deletions must be traceable to an approved offboarding "
     "or access removal request. Unexpected deletions must be investigated.",
     "_deleted"),
    (14, "Expired password(Password age> 90days)",
     "Expectations: Except service accounts , all active user accounts must have a password changed within the last 90 days. Accounts with expired passwords must be flagged to the system owner for immediate reset or formal justification.\n All services accounts must be changed on annually basis.\nNote:As per policy , password of service account must be changed every 365 days",
     "is_password_stale"),
    (15, "3PP (Third-Party Personnel)",
     "Expectations: Third-party accounts must be time-limited, regularly reviewed, "
     "and immediately disabled upon contract termination. Access must be restricted "
     "to the minimum required scope.",
     None),
    (16, "Administrator Accounts",
     "Expectations: Administrator and privileged accounts must be limited to named "
     "individuals with a documented business justification. Shared admin accounts "
     "are not permitted. All admin actions must be logged and monitored.",
     "is_privileged_flag"),
    (17, "Annual User Profile and matrix review",
     "Expectations: The system owner must perform and document a full user access "
     "review at least annually, validating that all access rights remain appropriate "
     "and aligned with current job responsibilities.",
     None),
    (18, "Terminated Users and Transferred users",
     "Expectations: Access must be revoked within 24 hours of employee departure or "
     "role change. The system owner must cross-reference the HR termination and "
     "transfer lists against active accounts and confirm all departures are addressed.",
     "is_terminated_but_active"),
    (19, "First line user access review report and accuracy",
     "Expectations: The system owner must perform monthly user access reviews using "
     "the approved template and confirm that the review report is complete, "
     "accurate, and supported by sufficient evidence.",
     None),
]

CONCLUSION_HEADING = "V. CONCLUSION"

