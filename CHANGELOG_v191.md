# v191 — Cohérence sommaire / détail des rapports + libellés

## Vérification des corrections précédentes (sur v185)
Présentes : statut résolu (mappings manuels) pour orphelins, « parti mais actif », doublons,
transferts, fichier des partis, réactivés, contrôle qualité ; revue précédente analysée avec
mappings + sa date ; date Excel de la revue précédente.
Manquante (restaurée) : suivi des orphelins dans l'historique de tendance (trend_tracking).

## Incohérence sommaire / nombre de « Expired password » (Ctrl 14)
Cause : le sommaire du rapport WORD recomptait chaque contrôle sur TOUS les comptes
(inactifs inclus), alors que chaque section détail ne garde que les comptes ACTIFS hors
risques acceptés (PDF, Excel et dashboard étaient corrects). Touchait aussi Ctrl 16 (admins).
- reporting/export.py : le sommaire Word utilise compute_control_coverage (même source que
  PDF / Excel / dashboard). Le Summary Excel reçoit aussi les stats de comparaison (Ctrl 10-13
  restaient N/A malgré une revue précédente).
- dashboard/app.py : KPI « Dormants » et « MDP périmé » = mêmes chiffres que la grille des contrôles.
- analysis/access_review.py : summarize()["password_stale"] = comptes actifs uniquement.

## Libellés
- Ctrl 9 : « MTN LIBERIA naming convention » -> « MTN naming convention ».
- Ctrl 10 (Accounts created) : nouvelle expectation (ticket ServiceNow / user access form approuvé,
  ID à communiquer par le system owner).

## Tests
tests/test_cross_module_consistency.py (+4), tests/test_access_review.py et
tests/test_trend_tracking.py (tests orphelins/tendance du v188 réintégrés).

## Interface du dashboard (zone principale, à droite de la barre latérale)
- Plus aucun emoji : KPI (« Dormants », « MDP périmé »), message d'accueil, libellé et onglet
  « Configuration du fichier » (« — à vérifier »), fiche compte (« Privilégié : Oui »), libellés de
  statut de l'écran de configuration (analysis/access_review.py). La barre latérale est inchangée.
- Grille « Couverture des contrôles IAM » : palette sobre identique aux rapports PDF
  (vert #0E6E57 conforme, brique #A13D2E anomalie, gris N/A), fond neutre, filet fin ; plus d'icônes
  ni de pastels vifs.
- tests/test_dashboard.py : la règle « aucun emoji à droite » n'a plus d'exceptions cachées
  (l'ancienne liste laissait passer les symboles d'alerte et les pastilles de couleur) ;
  nouveau test de palette.
