# Guide d'installation — Access Review & IAM Anomaly Detection Toolkit

## 0. Prérequis (une seule fois par machine)

Vérifie que Python est installé :
```bash
python3 --version
```
(Sur Windows, essaie `python --version` si `python3` ne fonctionne pas.)

Si absent : télécharge sur [python.org](https://www.python.org/downloads/) — coche "Add Python to PATH" à l'installation sur Windows.

Aucun autre logiciel n'est nécessaire — pas de Git, pas de compte GitHub.

---

## 1. Récupérer le projet depuis SharePoint

1. Ouvre le lien SharePoint : **[À COMPLÉTER — lien vers le dossier/fichier SharePoint]**
2. Télécharge le fichier `access_review_toolkit_CORRIGE_vXX.zip` (prends toujours la version la plus récente si plusieurs sont présentes — le numéro le plus élevé).
3. Extrais l'archive :
   - **Windows** : clic droit sur le fichier zip → "Extraire tout..." → choisis un dossier (ex. `Documents\access-review-toolkit`).
   - **Mac** : double-clic sur le fichier zip, il s'extrait automatiquement à côté.
4. Ouvre un terminal et place-toi dans le dossier extrait :
```bash
cd chemin/vers/access_review_toolkit
```
(Sur Windows, tu peux taper `cd ` puis glisser-déposer le dossier extrait dans la fenêtre du terminal pour remplir le chemin automatiquement.)

---

## 2. Créer et activer l'environnement virtuel

**Mac / Linux :**
```bash
python3 -m venv venv
source venv/bin/activate
```

**Windows (PowerShell) :**
```powershell
python -m venv venv
venv\Scripts\Activate.ps1
```

**Windows (cmd) :**
```cmd
python -m venv venv
venv\Scripts\activate.bat
```

Tu sais que c'est activé quand tu vois `(venv)` au début de la ligne de commande.

> Sur un PC professionnel, si PowerShell bloque l'exécution du script d'activation (erreur de policy), lance d'abord :
> ```powershell
> Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
> ```

---

## 3. Installer les dépendances

```bash
pip install -r requirements.txt
```

(Sur certains PC pro avec proxy d'entreprise, ajoute si besoin :)
```bash
pip install -r requirements.txt --proxy http://votre-proxy:port
```

---

## 4. Vérifier que tout fonctionne

Deux façons, la première suffit dans la majorité des cas :

**Vérification simple, pensée pour tout le monde** — pas besoin de connaître pytest :
```bash
python3 verify_installation.py
```
Ce script simule un vrai usage de bout en bout (import, analyse, génération des 3 rapports) et affiche un résultat clair en français : soit tout est OK, soit il indique précisément quoi rapporter à quelqu'un qui peut dépanner.

**Vérification complète (pour qui est à l'aise techniquement)** :
```bash
pip install pytest
pytest tests/ -v
```
Tous les tests doivent passer (~265 tests). Si l'un d'eux échoue, vérifie d'abord que l'étape 3 s'est bien terminée sans erreur avant de chercher plus loin.

---

## 5. Lancer le dashboard

```bash
streamlit run dashboard/app.py
```

Si `streamlit` n'est pas reconnu comme commande :
```bash
python3 -m streamlit run dashboard/app.py
```

Le navigateur s'ouvre automatiquement sur `http://localhost:8501`.

---

## 6. Utilisation en ligne de commande (sans dashboard)

```bash
python3 -m analysis.access_review data/scenario_iam_export.csv
python3 -m analysis.hr_crossref data/scenario_iam_export.ldif data/HR_scenario_hr_export.csv
python3 -m analysis.sod_detection data/example_iam_with_sod_conflict.csv
python3 -m reporting.export data/scenario_iam_export.csv
```

---

## 7. Récupérer une mise à jour (nouvelle version déposée sur SharePoint)

Il n'y a pas de commande "mettre à jour" comme avec Git — il faut retélécharger et réextraire :

1. Retourne sur le lien SharePoint et télécharge la dernière version du zip.
2. Extrais-la dans un **nouveau dossier** (ne pas écraser l'ancien directement, pour ne pas perdre tes propres décisions de revue si tu en as déjà enregistré — voir la section Confidentialité du README).
3. Si tu avais des fichiers dans `data/` propres à ton usage (décisions de revue, correspondances de colonnes apprises, acceptations de risque, historique de tendance), copie-les depuis l'ancien dossier vers le nouveau avant de continuer :
   - `data/review_decisions.json`
   - `data/custom_column_mappings.json`, `data/custom_hr_column_mappings.json`, `data/custom_transfer_column_mappings.json`
   - `data/risk_acceptances.json`
   - `data/trend_history.json`
4. Recommence à partir de l'étape 2 (nouvel environnement virtuel) dans le nouveau dossier.

---

## 8. Récapitulatif express (copier-coller direct, une fois le zip extrait)

```bash
cd chemin/vers/access_review_toolkit
python3 -m venv venv
source venv/bin/activate          # Windows : venv\Scripts\activate
pip install -r requirements.txt
streamlit run dashboard/app.py
```

---

## Dépannage rapide

| Problème | Solution |
|---|---|
| `command not found: streamlit` | Utilise `python3 -m streamlit run dashboard/app.py` |
| `command not found: python3` (Windows) | Utilise `python` à la place |
| PowerShell bloque l'activation du venv | `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` |
| Erreur de proxy entreprise sur `pip install` | Demande l'URL du proxy au service IT, ajoute `--proxy http://...` |
| `pip: command not found` | Utilise `python3 -m pip install -r requirements.txt` |
| Le terminal reste bloqué sur `quote>` | Une apostrophe non fermée dans la commande — tape `'` puis Entrée, ou Ctrl+C pour annuler |
| Le zip SharePoint refuse de s'ouvrir / "fichier corrompu" | Retélécharge-le — un téléchargement interrompu produit souvent ce message |
