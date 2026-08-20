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

- **CSV** (`.csv`)
- **Excel** (`.xlsx`, `.xls`)
- **Word** (`.docx`) :
  1. Cherche d'abord un tableau dans le document (le plus pertinent s'il y
     en a plusieurs), même entouré de texte libre (titre, intro, notes).
  2. Si aucun tableau, retombe sur la même lecture en cascade que le texte
     libre (voir ci-dessous), appliquée au contenu des paragraphes.
- **Texte brut** (`.txt`) : trois stratégies essayées dans l'ordre, la
  première qui produit un résultat exploitable est retenue :
  1. **Délimité** : virgule, point-virgule, tabulation ou pipe — cas d'un
     export brut simplement enregistré en `.txt`.
  2. **Colonnes alignées par espaces** : rapports générés en ligne de
     commande ou exports de systèmes legacy.
  3. **Blocs clé-valeur** : une fiche par enregistrement, séparée par des
     lignes vides, au format `clé: valeur` ou `clé= valeur` (avec ou sans
     puce) — ex. des fiches individuelles collées dans un compte-rendu.

  Si aucune des trois stratégies ne produit de structure reconnaissable,
  l'erreur l'indique clairement plutôt que d'échouer silencieusement ou de
  produire des données incohérentes.

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
