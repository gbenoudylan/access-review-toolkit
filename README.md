# Access Review & IAM Anomaly Detection Toolkit

Outil d'ingestion universelle et de détection automatique des anomalies
d'accès (comptes orphelins, dormants, privilèges non justifiés), conçu pour
absorber des exports IAM à formats variables sans réécriture de code.

Projet mené en parallèle du stage Data Protection & IAM chez MTN Côte d'Ivoire.

## Statut du projet

- [x] **Phase 1 — Ingestion universelle** (adaptée du projet vulnerability tracker)
- [x] **Phase 2 — Détection des anomalies d'accès** (terminée)
- [x] **Phase 3 — Dashboard Streamlit** (terminée)
- [x] **Phase 4 — Export du rapport de revue (Excel/PDF)** (terminée)

**Projet complet.**

## Le problème résolu

Une revue d'accès périodique (access review) doit répondre à des questions
simples mais critiques : *quels comptes appartiennent à des personnes
parties ? lesquels ne se sont pas connectés depuis des mois ? qui a des
droits privilégiés sans justification claire ?* Fait manuellement sur un
export brut, ce travail est long et sujet à l'oubli. Cet outil l'automatise.

## Anomalies détectées

| Anomalie | Critère | Niveau de risque |
|---|---|---|
| **Compte actif d'un employé parti** | `account_status` actif + `employee_status` terminé | Critique |
| **Compte privilégié dormant** | Pas de connexion depuis > 90 jours + droits admin | Critique |
| **Compte standard dormant** | Pas de connexion depuis > 90 jours | Moyen/Élevé |
| **Compte sans owner identifié** | Champ `manager` vide | Moyen |

Chaque compte reçoit une **action recommandée** ("Révoquer immédiatement",
"Désactiver", "Identifier un owner"...) et un **niveau de risque**, pour
transformer un export brut en plan d'action priorisé.

## Architecture

Reprend la même architecture validée sur le projet de gestion des
vulnérabilités (voir `vuln_tracker`), adaptée au domaine IAM :

```
Export d'accès (n'importe quel format)
        │
        ▼
 config/column_mapping.py   -> référentiel des variantes de colonnes IAM
        │
        ▼
 ingestion/ingest.py        -> détection d'en-tête, standardisation
        │
        ▼
 analysis/access_review.py  -> détection des anomalies, scoring de risque
```

### Formats de fichiers acceptés en entrée

| Format | Comportement |
|---|---|
| **CSV** (`.csv`) | Détection automatique du séparateur, gestion des lignes de longueur inégale |
| **Excel** (`.xlsx`, `.xls`) | Lecture directe de la première feuille |
| **Word** (`.docx`) | Cherche un tableau (le plus pertinent s'il y en a plusieurs) ; à défaut, retombe sur la lecture en cascade du texte des paragraphes (voir Texte brut) |
| **Texte brut** (`.txt`) | 3 stratégies en cascade — voir détail ci-dessous |
| **JSON** (`.json`) | Liste d'objets, ou objet contenant une liste sous une clé courante (`results`, `data`, `users`, `accounts`, `records`, `items`, `value`) |
| **XML** (`.xml`) | Éléments répétitifs représentant chacun un compte (ex. `<user>...</user>`) |
| **HTML** (`.html`, `.htm`) | Le tableau le plus pertinent parmi ceux présents dans la page (export copié depuis une page web/intranet) |
| **LDIF** (`.ldif`) | Export natif LDAP/Active Directory. Décode automatiquement `userAccountControl` (bitmask) en statut Active/Disabled lisible. Ajoute une colonne `system` par défaut (un LDIF représente un seul annuaire, donc l'info n'existe structurellement pas dans les données) |
| **PDF** (`.pdf`) | Extraction de tableau par bordures visibles, avec repli sur une détection par alignement de texte si aucune bordure n'est trouvée (moins fiable — un avertissement est loggé dans ce cas) |
| **ZIP** (`.zip`) | Extrait et traite chaque fichier supporté à l'intérieur, puis combine tous les résultats en un seul jeu de données. Un fichier illisible dans l'archive est ignoré (avec avertissement) sans faire échouer les autres |

**Stratégies pour le texte brut** (`.txt`, et repli du Word/LDIF sans structure claire), essayées dans l'ordre jusqu'à ce que l'une fonctionne :
1. **Délimité** : virgule, point-virgule, tabulation ou pipe.
2. **Colonnes alignées par espaces** : rapports en ligne de commande, exports legacy.
3. **Blocs clé-valeur** : une fiche par enregistrement, séparée par des lignes vides, au format `clé: valeur`.

Si aucune stratégie ne produit de structure reconnaissable, l'erreur l'indique clairement plutôt que d'échouer silencieusement ou de produire des données incohérentes.

### Limites connues, assumées

- **PDF sans bordures visibles** : l'extraction par alignement de texte peut mal découper certaines colonnes ou lignes. Un PDF avec un vrai tableau (bordures) est toujours plus fiable.
- **Formats volontairement exclus** : OCR sur image/scan (peu fiable, hors périmètre), fichiers `.eml` (mieux vaut en extraire la pièce jointe séparément). Ces cas sont rares et mieux traités au cas par cas qu'en complexifiant le pipeline pour un gain marginal.

### Colonnes reconnues

`username`, `full_name`, `email`, `department`, `job_title`, `manager`,
`system`, `role`, `account_status`, `is_privileged`, `last_login_date`,
`account_created_date`, `employee_status`.

Seuls `username` et `system` sont obligatoires — plus il y a de colonnes
disponibles, plus l'analyse est précise, mais l'outil ne plante jamais
faute de colonne optionnelle manquante (juste un avertissement en log).

## Utilisation

```bash
pip install -r requirements.txt
python -m analysis.access_review data/export_test_A.csv
```

## Tests

Deux fichiers de test aux formats différents sont fournis (`data/`), avec
des données **entièrement synthétiques** (noms et emails fictifs, pas de
données personnelles réelles) :
- `export_test_A.csv` : format anglais standard
- `export_test_B.csv` : format français, en-tête décalée, colonnes
  réordonnées, lignes de méta-données parasites

```bash
python tests/test_access_review.py
```

6 tests valident : la détection des comptes terminés-mais-actifs, la
détection de dormance, la non-détection sur connexion récente, la
priorisation des comptes privilégiés dormants, l'absence de plantage sans
colonnes optionnelles, et l'exactitude du résumé chiffré.

## Prochaines étapes

- ~~Phase 3 : dashboard Streamlit~~ ✅
- ~~Phase 4 : export Excel/PDF~~ ✅

## Utilisation du dashboard

```bash
streamlit run dashboard/app.py
```

Upload d'un fichier (ou utilisation du fichier d'exemple), visualisation des
comptes par niveau de risque, filtres, et génération de rapports Excel/PDF
directement depuis l'interface — en ne gardant que la sélection filtrée
(par exemple, uniquement les comptes "Critique" pour un rapport ciblé).

## Utilisation en ligne de commande (export direct)

```bash
python -m reporting.export data/export_test_A.csv
```
Génère `output/rapport_revue_acces.xlsx` et `.pdf`.
