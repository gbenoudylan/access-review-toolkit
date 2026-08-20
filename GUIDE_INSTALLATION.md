# Guide d'installation — Vulnerability Tracker & Access Review Toolkit

## 0. Prérequis (une seule fois par machine)

Vérifie que Python est installé :
```bash
python3 --version
```
(Sur Windows, essaie `python --version` si `python3` ne fonctionne pas.)

Si absent : télécharge sur [python.org](https://www.python.org/downloads/) — coche "Add Python to PATH" à l'installation sur Windows.

Vérifie Git :
```bash
git --version
```
Si absent, installe [Git](https://git-scm.com/downloads), ou utilise [GitHub Desktop](https://desktop.github.com) si tu préfères l'interface graphique.

---

## 1. Récupérer le projet depuis GitHub

### Access Review Toolkit
```bash
git clone https://github.com/gbenoudylan/access-review-toolkit.git
cd access-review-toolkit
```

### Vulnerability Tracker
```bash
git clone https://github.com/gbenoudylan/vuln_tracker_phase1.git
cd vuln_tracker_phase1
```

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

## 4. Lancer les tests (vérifier que tout fonctionne)

**Access Review Toolkit :**
```bash
python3 tests/test_access_review.py
python3 tests/test_export.py
python3 tests/test_docx_ingestion.py
python3 tests/test_universal_text_ingestion.py
python3 tests/test_extended_formats.py
python3 tests/test_enrichments.py
```

**Vulnerability Tracker :**
```bash
python3 tests/test_enrichment.py
python3 tests/test_scoring.py
python3 tests/test_export.py
```

(Remplace `python3` par `python` sur Windows si nécessaire.)

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

**Access Review Toolkit :**
```bash
python3 -m analysis.access_review data/scenario_iam_export.csv
python3 -m analysis.hr_crossref data/scenario_iam_export.ldif data/HR_scenario_hr_export.csv
python3 -m analysis.sod_detection data/example_iam_with_sod_conflict.csv
python3 -m reporting.export data/scenario_iam_export.csv
```

**Vulnerability Tracker :**
```bash
python3 -m enrichment.cvss_enrichment data/export_format_A.csv
python3 -m scoring.composite_score data/export_format_A.csv
```

---

## 7. Récupérer les mises à jour depuis GitHub (si tu as déjà cloné avant)

```bash
git pull
pip install -r requirements.txt
```

---

## 8. Récapitulatif express (copier-coller direct)

```bash
git clone https://github.com/gbenoudylan/access-review-toolkit.git
cd access-review-toolkit
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
