# Guide d'utilisation — Access Review & IAM Anomaly Detection Toolkit

Ce guide couvre **tout** ce que fait l'outil, dans l'ordre où tu le
rencontres à l'écran — de l'ouverture de l'application jusqu'à la
génération du rapport final. Pour l'installer (une seule fois par
machine), réfère-toi au **Guide d'installation** séparé.

---

## 1. Ouvrir l'application

À chaque fois que tu veux utiliser l'outil :

1. Ouvre un terminal.
2. Place-toi dans le dossier du projet :
   ```bash
   cd chemin/vers/access_review_toolkit
   ```
3. Active l'environnement virtuel (créé une seule fois à l'installation) :
   - **Mac/Linux** : `source venv/bin/activate`
   - **Windows (PowerShell)** : `venv\Scripts\Activate.ps1`
   - **Windows (cmd)** : `venv\Scripts\activate.bat`
   Tu dois voir `(venv)` apparaître au début de la ligne.
4. Lance l'application :
   ```bash
   streamlit run dashboard/app.py
   ```
5. Un navigateur s'ouvre automatiquement sur `http://localhost:8501`. Si
   ce n'est pas le cas, copie cette adresse toi-même dans ton navigateur.

**Pour arrêter l'application** : reviens dans le terminal et fais
`Ctrl+C`. Fermer juste l'onglet du navigateur ne suffit pas — l'outil
continue de tourner en arrière-plan tant que le terminal reste ouvert.

**L'écran se divise en deux** :
- Une **barre latérale à gauche** — tout ce que tu importes et configures.
- Une **zone principale à droite** — tout ce que l'analyse produit.

---

---

## 2. Sauvegarder les données de l'outil (recommandé)

Tout en haut de la barre latérale, un encadré **"Sauvegarde des données
de l'outil"** te permet de télécharger en un clic tout ce que l'outil a
appris ou enregistré (décisions de revue, correspondances de colonnes
apprises, acceptations de risque, historique de tendance). Fais-le de
temps en temps, et surtout avant de changer de machine — sans cette
sauvegarde, tout redémarrerait de zéro sur une nouvelle installation.

---

## 3. Importer ton fichier d'export d'accès

Dans la barre latérale, section **Import** :

1. Clique sur **Upload** sous "Export d'accès" et choisis ton fichier.
   Le format n'a pas d'importance — Excel, CSV, Word, PDF, une image
   scannée, une archive ZIP contenant plusieurs fichiers... tout est
   accepté tel quel, sans préparation de ta part.
2. **Nom du système** — si ton fichier ne précise pas lui-même de quel
   système proviennent les comptes (AD, SAP, SIEM...), renseigne-le ici.
   Sinon laisse vide.
3. **Date d'extraction de ce fichier** — la date à laquelle le fichier a
   été généré. Sert de référence pour calculer l'ancienneté des
   connexions et des comptes créés. Laisse vide pour utiliser la date du
   jour par défaut.

L'analyse se lance automatiquement dès qu'un fichier est importé — pas
besoin de cliquer sur un bouton "Lancer".

### Si une colonne n'est pas reconnue

Juste après l'import, si une colonne de ton fichier n'a pas pu être
reconnue automatiquement (un intitulé inhabituel, propre à ton
entreprise), un encadré **"Colonnes non reconnues ou champs vides"**
apparaît, déplié automatiquement si un champ important manque. Pour
chaque colonne listée :

1. Choisis dans le menu déroulant à quel champ standard elle correspond
   (ex. "dernière connexion", "statut du compte"...).
2. Clique sur **Enregistrer ces correspondances et relancer l'analyse**.

**Cette correction est mémorisée** : la prochaine fois qu'un fichier
contient une colonne portant exactement ce même nom, elle sera reconnue
automatiquement, sans que tu aies à recommencer.

**Cas particulier — un indicateur inversé** : si la colonne correspond
au statut du compte mais sous forme d'un "vrai/faux" signifiant
l'inverse (ex. une colonne "accountDisabled" où *vrai* veut dire que le
compte est désactivé), coche la case **"est un indicateur inversé"**
qui apparaît à côté — sans ça, le sens serait interprété à l'envers.

### Revoir ou corriger une association déjà faite

Si une correction a été enregistrée par erreur, un encadré
**"Correspondances déjà apprises"** liste tout ce qui a été mémorisé,
avec un bouton **Retirer** pour chacune. Une fois retirée, la colonne
réapparaît comme non reconnue et peut être réassignée correctement.

---

## 4. Croisement RH (optionnel)

Sous **Croisement RH**, dans la barre latérale : importe un export RH
(qui est vraiment employé) pour que l'outil confirme, pour chaque
compte, si la personne est toujours dans l'entreprise. Le fonctionnement
(colonnes non reconnues, correction, mémorisation) est **identique** à
celui du fichier principal, avec son propre magasin de correspondances
séparé.

---

## 5. Comptes transférés/mutés (optionnel)

Sous **Comptes transférés/mutés** : importe le fichier RH de mouvements
de personnel (le classeur qui contient, entre autres, une feuille
"Affectation/Mutation"). L'outil détecte automatiquement la bonne
feuille parmi celles du classeur — si elle n'est pas trouvée, renseigne
son nom exact dans le champ **"Nom exact de la feuille"**.

Sert à repérer les comptes de personnes mutées vers un autre service
mais dont l'accès à l'ancien système est resté actif. Même mécanisme de
colonnes non reconnues que les autres fichiers.

---

## 6. Seuils des contrôles

Trois curseurs ajustent les seuils utilisés par l'analyse :
- **Seuil de dormance** — au-delà de combien de jours sans connexion un
  compte est considéré dormant (90 par défaut).
- **Seuil d'ancienneté du mot de passe** — au-delà de combien de jours un
  mot de passe est considéré périmé (90 par défaut).
- **Seuil "jamais utilisé"** — au-delà de combien de jours depuis la
  création un compte jamais utilisé est signalé (30 par défaut).

Laisse ces valeurs par défaut sauf instruction contraire de ton
référentiel de contrôle interne.

---

## 7. Matrice SoD personnalisée (optionnel)

Par défaut, l'outil détecte les conflits de séparation des tâches (SoD)
avec une matrice générique (ex. créateur de paiement + validateur de
paiement). Pour utiliser la tienne : prépare un fichier à deux colonnes
(rôle 1, rôle 2 — peu importe leur nom, seules les deux premières
colonnes comptent), une paire de rôles incompatibles par ligne, puis
importe-le ici.

---

## 8. Vue d'ensemble

En haut de la zone principale, six chiffres résument la situation :
comptes analysés, employés partis avec un accès encore actif, comptes
dormants, conflits SoD, taux de revue déjà traité, et comptes
privilégiés à mot de passe permanent.

Juste en dessous, un graphique montre la répartition des comptes par
niveau de risque actif (Critique / Élevé / Moyen / Faible) — les comptes
dont le risque a été formellement accepté (voir section 9) n'y
apparaissent plus, exactement comme ils disparaissent des comptages
actifs : ce ne sont plus des risques en attente de traitement.

### Qualité des données

Un encadré dépliable indique la fiabilité estimée du fichier source
(identifiants manquants, doublons, dates illisibles, dates dans le
futur...) — une vérification préalable, avant même les contrôles IAM,
purement informative.

### Control Coverage

Un autre encadré indique combien des 19 contrôles standards ont pu être
réellement exécutés sur ton fichier. Certains contrôles nécessitent une
information que tous les exports ne contiennent pas — c'est normal et
indiqué clairement ("N/A"), jamais un chiffre inventé à la place.

---

## 9. Détail des comptes

Un tableau liste tous les comptes analysés, avec pour chacun son
système, son statut, l'action recommandée, et son niveau de risque.
Filtre par niveau de risque pour te concentrer d'abord sur les cas les
plus urgents, ou coche **"Actions requises uniquement"** pour ne voir
que les comptes qui appellent vraiment une décision.

Un bouton **Télécharger en CSV** permet d'exporter ce tableau tel quel.

---

## 10. Investigation de compte

Sélectionne un compte dans la liste déroulante pour voir sa fiche
complète : identité, accès, activité, la liste des anomalies détectées
(Findings), et le détail du calcul de son score de risque.

### Accepter un risque (constat par constat)

Si un ou plusieurs problèmes sont détectés sur ce compte, la section
**Acceptation de risque** te permet, une fois que le propriétaire du
compte a répondu et justifié la situation, d'accepter formellement **le
constat précis** concerné (ex. "Compte dormant") — pas le compte dans
l'absolu. Si ce même compte a par ailleurs un autre problème (ex. un
conflit SoD), celui-ci reste actif et signalé tant qu'il n'a pas été,
lui aussi, spécifiquement accepté.

1. Choisis dans **"Constat à accepter"** le problème concerné (si
   plusieurs sont détectés, chacun s'accepte séparément).
2. Coche **"Prévoir une échéance de revalidation"** si l'acceptation ne
   doit être valable que temporairement, puis choisis la date.
3. Renseigne la justification (obligatoire) et ton nom.
4. Clique sur **Accepter ce risque**.

Le compte disparaît alors du tableau de ce contrôle précis et de tous
les comptages associés — mais reste visible, avec le détail de sa
justification, dans la section **Exceptions** du rapport final.

**Si l'échéance est dépassée**, le constat redevient automatiquement un
finding actif normal au prochain cycle — signalé distinctement ("à
revalider") plutôt que de redisparaître silencieusement.

**Pour retirer une acceptation** faite par erreur, le bouton **Retirer
cette acceptation** apparaît à la place du formulaire une fois le risque
accepté.

---

## 11. Validation de la revue

Pour chaque compte, décide d'un statut :
- **En attente** — pas encore traité
- **Validé - accès légitime** — l'accès est justifié, rien à faire
- **Révoqué** — l'accès doit être retiré

Renseigne ton nom comme validateur, ajoute un commentaire si besoin, puis
clique sur **Enregistrer les décisions**. Chaque décision est conservée
avec la date et l'historique complet, d'une session à l'autre — rien
n'est jamais perdu ou écrasé silencieusement.

---

## 12. Tendance dans le temps

Permet d'enregistrer volontairement un instantané des indicateurs clés
de ce cycle (renseigne une période, ex. "T1 2026", puis clique sur
**Enregistrer ce cycle dans l'historique de tendance**) pour suivre
leur évolution d'une revue à l'autre — pas seulement l'état du jour.
Charger le même fichier plusieurs fois n'ajoute rien à l'historique tant
que tu ne cliques pas sur le bouton.

---

## 13. Générer le rapport final

Tout en bas de la page, section **Rapports formatés** :

1. **Période couverte par ce rapport** — ex. "T1 2026". Laisse vide pour
   utiliser automatiquement le trimestre courant.
2. **Comparer avec la revue précédente (optionnel)** — importe le
   fichier de la revue d'un cycle antérieur (même format que l'export
   principal) pour que le rapport calcule automatiquement les comptes
   créés, supprimés, réactivés, et les profils modifiés entre les deux
   cycles. Même mécanisme de colonnes non reconnues que le fichier
   principal (et le même magasin de correspondances — une correction
   faite sur l'un s'applique aussi à l'autre).
3. **Logo** — optionnel, pour personnaliser l'en-tête du PDF/Word.
4. Trois boutons génèrent le rapport : **Excel**, **PDF**, ou **Word**.
   Les trois contiennent les mêmes résultats, présentés différemment :
   - **Excel** — pour le suivi opérationnel, ligne par ligne, jamais
     plafonné même sur un très grand nombre de comptes.
   - **PDF** — document d'audit prêt à diffuser, structuré selon le
     template officiel (19 contrôles, résumé exécutif, annexes,
     Exceptions).
   - **Word** — comme le PDF, mais modifiable si tu veux ajuster un
     commentaire avant de l'envoyer.

Le rapport se télécharge directement depuis le bouton une fois généré.

---

## Questions fréquentes

**Un contrôle affiche "N/A", c'est un problème ?**
Non. Ça signifie que ce contrôle a besoin d'une information que ton
fichier ne contient pas, ou qu'il dépend d'une méthode propre à
l'entreprise (ex. la convention de nommage — différente selon chaque
OPCO, jamais devinée). Ce n'est jamais un chiffre inventé à la place.

**Le pourcentage de fiabilité des données est bas, que faire ?**
Regarde le détail affiché (identifiants manquants, doublons, dates
illisibles ou dans le futur...) — ça pointe généralement un souci dans
le fichier source à corriger avant de refaire confiance à l'analyse.

**Je peux relancer l'analyse avec un autre fichier ?**
Oui, il suffit d'importer un nouveau fichier dans la barre latérale — la
page se met à jour automatiquement.

**Mes décisions de validation, correspondances de colonnes et
acceptations de risque sont-elles perdues si je ferme la page ?**
Non, une fois enregistrées elles sont conservées d'une session à
l'autre, sur cette machine.

**Un compte a plusieurs problèmes en même temps, j'en accepte un —
est-ce que les autres disparaissent aussi ?**
Non, jamais. Chaque acceptation ne couvre que le constat précis
accepté — un autre problème sur ce même compte reste actif et visible
tant qu'il n'a pas été, lui aussi, spécifiquement traité.

**J'ai fermé le terminal par erreur, comment je relance ?**
Reviens à l'étape 1 de ce guide (réactiver l'environnement, relancer
`streamlit run dashboard/app.py`) — rien n'est perdu, tes fichiers
enregistrés dans `data/` restent intacts.
