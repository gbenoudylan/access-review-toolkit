# Guide d'utilisation — Access Review Toolkit

Ce guide s'adresse à toute personne qui doit **utiliser** l'outil pour
mener une revue d'accès — pas l'installer ni le configurer. Si l'outil
n'est pas encore lancé sur ton poste, demande à la personne qui l'a mis en
place de l'ouvrir pour toi, ou réfère-toi au guide d'installation.

---

## 1. Ouvrir l'outil

Une fois lancé, l'outil s'ouvre dans ton navigateur. Tu verras une page
avec :
- Une **barre latérale à gauche** — pour importer ton fichier
- Une **zone principale** — où tout le résultat de l'analyse s'affiche

---

## 2. Importer ton fichier d'export

Dans la barre latérale, à gauche :

1. Clique sur **Import** et sélectionne ton fichier d'export d'accès
   (peu importe le format : Excel, CSV, Word, PDF, une archive ZIP — tu
   n'as rien à préparer avant, dépose le fichier tel quel).
2. Si tu as aussi un export RH (pour croiser les comptes avec le statut
   réel des employés), tu peux l'ajouter dans la section **Croisement RH
   (optionnel)** juste en dessous.
3. Les **seuils** (dormance, mot de passe, etc.) sont préréglés à des
   valeurs standards — tu peux les laisser tels quels sauf instruction
   contraire.

L'analyse se lance automatiquement dès qu'un fichier est importé.

---

## 3. Lire la vue d'ensemble

En haut de la page principale, six chiffres résument la situation :
comptes analysés, employés partis avec un accès encore actif, comptes
dormants, conflits de séparation des tâches, taux de revue déjà traité,
et comptes privilégiés à mot de passe permanent.

Juste en dessous, un graphique montre la répartition des comptes par
niveau de risque (Critique / Élevé / Moyen / Faible).

### Control Coverage

Un menu dépliable indique combien des 18 contrôles standards ont pu être
réellement exécutés sur ton fichier (certains contrôles nécessitent une
information que tous les exports ne contiennent pas — c'est normal et
indiqué clairement, pas une erreur).

---

## 4. Consulter le détail des comptes

Plus bas, un tableau liste tous les comptes analysés, avec pour chacun :
son système, son statut, l'action recommandée, et son niveau de risque.
Tu peux filtrer ce tableau par niveau de risque pour te concentrer
d'abord sur les cas les plus urgents.

---

## 5. Investiguer un compte précis

Dans la section **Investigation de compte**, sélectionne un compte dans
la liste déroulante pour voir sa fiche complète : identité, système,
dernière connexion, anomalies détectées, et le détail du calcul de son
score de risque (quelles raisons précises l'ont amené à ce score).

Si le même identifiant existe sur plusieurs systèmes, une seconde liste
te permet de choisir lequel.

---

## 6. Valider chaque compte (la revue proprement dite)

C'est l'étape centrale : pour chaque compte, tu dois décider d'un statut :
- **En attente** — pas encore traité
- **Validé - accès légitime** — l'accès est justifié, rien à faire
- **Révoqué** — l'accès doit être retiré

Renseigne ton nom comme validateur, ajoute un commentaire si besoin, puis
clique sur **Enregistrer les décisions**. Chaque décision est conservée
avec la date et l'historique complet — tu peux revenir dessus plus tard,
rien n'est jamais perdu ou écrasé silencieusement.

---

## 7. Générer le rapport final

Tout en bas de la page, trois boutons permettent de générer le rapport :
**Excel**, **PDF**, ou **Word**. Les trois contiennent les mêmes
résultats, présentés différemment :
- **Excel** : pour le suivi opérationnel, ligne par ligne
- **PDF** : document d'audit prêt à diffuser
- **Word** : comme le PDF, mais modifiable si tu veux ajuster un
  commentaire avant de l'envoyer

Le rapport se télécharge directement depuis le bouton une fois généré.

---

## Questions fréquentes

**Un contrôle affiche "N/A", c'est un problème ?**
Non. Ça signifie que ce contrôle a besoin d'une information que ton
fichier ne contient pas (ex. une convention de nommage propre à
l'entreprise). Ce n'est jamais un chiffre inventé à la place.

**Le pourcentage de fiabilité des données est bas, que faire ?**
Regarde le détail affiché (identifiants manquants, doublons, dates
illisibles...) — ça pointe généralement un souci dans le fichier source
à corriger avant de refaire confiance à l'analyse.

**Je peux relancer l'analyse avec un autre fichier ?**
Oui, il suffit d'importer un nouveau fichier dans la barre latérale — la
page se met à jour automatiquement.

**Mes décisions de validation sont-elles perdues si je ferme la page ?**
Non, une fois enregistrées elles sont conservées d'une session à l'autre.
