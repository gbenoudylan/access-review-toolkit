# Access Review & IAM Anomaly Detection Toolkit

Outil d'automatisation de la revue périodique des habilitations (Access
Review IAM) : ingestion universelle de tout export d'accès, application
des 19 contrôles standards du secteur, détection d'anomalies, workflow de
validation humaine, et production d'un rapport d'audit exploitable —
Excel, PDF ou Word.

Projet mené en parallèle du stage Data Protection & IAM chez MTN Côte
d'Ivoire.

## Le problème résolu

Une revue d'accès périodique doit répondre à des questions simples mais
critiques : *quels comptes appartiennent à des personnes parties ?
lesquels ne se sont pas connectés depuis des mois ? qui a des droits
privilégiés sans justification claire ? qui a changé de rôle depuis la
dernière revue ?* Fait manuellement sur un export brut — souvent dans un
format différent selon le système source — ce travail est long, sujet à
l'erreur, et rarement traçable. Cet outil l'automatise de bout en bout.

## Fonctionnalités

**Ingestion universelle** — CSV, Excel (mono/multi-feuilles), Word, JSON,
XML, HTML, LDIF (export LDAP/AD natif), PDF, images scannées (OCR), ou une
archive ZIP contenant plusieurs de ces formats. Reconnaissance automatique
des colonnes par correspondance approximative, fusion intelligente quand
plusieurs sources décrivent les mêmes comptes.

**Les 19 contrôles standards d'une revue IAM** — comptes dormants, jamais
utilisés, orphelins, de test, de service, en doublon, mots de passe
périmés, comptes administrateurs, employés partis mais encore actifs,
convention de nommage, et plus. Chaque contrôle produit une liste
nominative complète (pas seulement un chiffre), avec les colonnes qui
justifient le classement et l'action recommandée — sans plafond, ce sont
les sections effectivement revues.

**Score de risque explicable (0-100)** — en plus du niveau catégoriel
(Critique/Élevé/Moyen/Faible), chaque compte a un score additif détaillé
ligne par ligne : *pourquoi* ce compte est à 100/100, pas seulement qu'il
l'est.

**Détection SoD (Separation of Duties)** — repère les cumuls de rôles
incompatibles, avec une matrice personnalisable chargeable depuis un
simple fichier Excel/CSV.

**Comparaison entre deux cycles de revue** — comptes créés, supprimés,
réactivés, profils modifiés, et détection spécifique des escalades de
privilège (un compte standard devenu administrateur).

**Contrôle qualité des données** — avant même l'analyse IAM, vérifie la
fiabilité du fichier source (identifiants manquants, doublons, dates
illisibles, statuts non reconnus) avec un pourcentage de fiabilité.

**Workflow de validation avec audit trail complet** — chaque compte peut
être marqué Validé / Révoqué / En attente, avec commentaire et validateur.
L'historique complet est conservé (pas seulement la dernière décision)
pour répondre à *qui a validé quoi, quand, et pourquoi*.

**Trois formats de restitution, cohérents entre eux** — PDF et Word fidèles
à un template d'audit officiel (mêmes calculs, moteurs de rendu
différents ; Word reste modifiable après génération), et Excel pour le
suivi opérationnel.

**Dashboard interactif (Streamlit)** — vue d'ensemble, répartition par
risque, Control Coverage, détail des comptes filtrable, fiche
d'investigation par compte, validation en ligne, génération des rapports.

## Architecture

```
Export d'accès (n'importe quel format)
        │
        ▼
 config/column_mapping.py     -> référentiel des variantes de colonnes IAM
        │
        ▼
 ingestion/ingest.py          -> détection d'en-tête, standardisation,
        │                        contrôle qualité des données
        ▼
 analysis/access_review.py    -> les 19 contrôles, score de risque,
        │                        action recommandée
        ▼
 analysis/sod_detection.py    -> conflits de séparation des tâches
 analysis/hr_crossref.py      -> croisement avec un export RH
 analysis/review_workflow.py  -> validation humaine, audit trail
        │
        ▼
 reporting/export.py          -> génération Excel / PDF / Word
        │
        ▼
 dashboard/app.py             -> interface Streamlit
```

### Formats de fichiers acceptés en entrée

| Format | Comportement |
|---|---|
| **CSV** (`.csv`) | Détection automatique du séparateur, encodage, lignes de longueur inégale |
| **Excel** (`.xlsx`, `.xls`) | Mono ou multi-feuilles, fusion automatique si plusieurs feuilles décrivent les mêmes comptes |
| **Word** (`.docx`) | Un ou plusieurs tableaux, avec repli sur la lecture du texte si absent |
| **Texte brut** (`.txt`) | Délimité, colonnes alignées, ou blocs clé-valeur — 3 stratégies en cascade |
| **JSON** (`.json`) | Liste d'objets, ou objet contenant une liste sous une clé courante |
| **XML** (`.xml`) | Éléments répétitifs représentant chacun un compte |
| **HTML** (`.html`, `.htm`) | Le tableau le plus pertinent de la page |
| **LDIF** (`.ldif`) | Export LDAP/AD natif, décodage de `userAccountControl` en statut lisible |
| **PDF** (`.pdf`) | Extraction de tableau par bordures, repli sur alignement de texte sinon |
| **Images** (`.png`, `.jpg`, `.jpeg`) | OCR (avec avertissement explicite sur la fiabilité) |
| **ZIP** (`.zip`) | Traite chaque fichier supporté à l'intérieur et combine les résultats |

Seuls `username` et `system` sont obligatoires — l'outil ne plante jamais
faute d'une colonne optionnelle manquante, il désactive juste le contrôle
concerné avec un avertissement explicite.

## Installation

```bash
pip install -r requirements.txt
```

## Utilisation

### Dashboard (recommandé)

```bash
streamlit run dashboard/app.py
```

Import du fichier (ou fichier d'exemple fourni), seuils des contrôles
configurables, listes d'employés partis et transférés en option,
détail des comptes filtrable, fiche d'investigation, validation, et
génération des rapports Excel/PDF/Word en un clic.

### Ligne de commande

```bash
python3 -m analysis.access_review data/export_test_A.csv
python3 -m reporting.export data/export_test_A.csv
```

## Tests

```bash
pytest tests/ -v
```

**284 tests automatisés**, dont la majorité couvrent des cas réels
rencontrés en pratique (formats de date ambigus selon la région ou le
système source, encodages, caractères non-latins, structures de fichiers
inhabituelles, valeurs tronquées) plutôt que des scénarios uniquement
synthétiques.

## Limites connues, assumées

- **PDF sans bordures visibles** : l'extraction par alignement de texte
  peut mal découper certaines colonnes.
- **OCR sur image** : moins fiable qu'un export structuré — signalé
  explicitement dans le rapport si utilisé.
- **Ambiguïté de date sans preuve dans la colonne** : si aucune valeur
  d'une colonne ne permet de trancher entre jour-premier et mois-premier,
  l'ambiguïté reste réellement insoluble mathématiquement (repli sur le
  standard jour-premier par défaut).
- **Contrôles nécessitant une configuration propre à l'entreprise**
  (comptes orphelins par rapprochement RH avancé, tiers 3PP, revue
  annuelle des profils) : marqués N/A avec l'explication précise plutôt
  qu'un chiffre inventé.

## Confidentialité

`data/review_decisions.json` peut contenir des décisions de revue réelles
(commentaires, noms de validateurs) une fois l'outil utilisé en
conditions réelles — à exclure de tout dépôt public ou partagé, au même
titre que tout export IAM réel placé dans `data/`.
