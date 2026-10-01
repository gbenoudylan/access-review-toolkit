# v189 — Cohérence du statut « actif » et des paramètres entre modules

Cause commune : plusieurs modules relisaient `account_status` avec leur propre
règle (liste codée en dur) au lieu de la décision d'`analyze_access`
(`is_active_for_audit` = mappings manuels > reconnaissance auto > pire cas).

| Fichier | Correction |
|---|---|
| analysis/access_review.py | Orphelins (ctrl 3), « parti mais actif » (ctrl 18) et doublons (ctrl 8) utilisent `is_active_for_audit` |
| analysis/hr_crossref.py | « Transféré mais actif » utilise `is_active_for_audit` |
| dashboard/app.py | Fichier des partis : même statut résolu ; revue précédente analysée avec mappings de statut/droits + SA date d'extraction ; rapport Excel recevait la liste des colonnes non reconnues à la place de la date de la revue précédente |
| reporting/export.py | Comptes « réactivés » comparés sur le statut résolu des deux cycles |
| ingestion/ingest.py | Contrôle qualité : « statut inconnu » reprend `status_is_unknown` (mappings manuels + formats composés) |
| tests/ | test_access_review.py (+1), test_cross_module_consistency.py (nouveau, 2 tests) |
