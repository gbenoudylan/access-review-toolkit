#!/usr/bin/env python3
"""
Auto-vérification de l'installation — à lancer une fois juste après
avoir installé l'outil (voir GUIDE_INSTALLATION.md), ou n'importe
quand pour confirmer que tout fonctionne encore sur cette machine.

Contrairement à `pytest tests/` (qui vérifie des dizaines de cas
précis et suppose une familiarité avec les tests automatisés), ce
script simule le VRAI usage de bout en bout — importer un fichier,
l'analyser, générer les 3 formats de rapport — et affiche un résultat
en français, compréhensible sans connaissance technique. Pensé pour
la situation où plus personne ne connaît le code : n'importe qui doit
pouvoir lancer

    python3 verify_installation.py

et savoir immédiatement si l'outil est utilisable ou s'il faut
solliciter une aide technique (auquel cas ce script indique
précisément quoi rapporter).
"""
import sys
import tempfile
import traceback
from pathlib import Path

CHECKS_PASSED = []
CHECKS_FAILED = []


def _check(label, fn):
    try:
        fn()
        CHECKS_PASSED.append(label)
        print(f"  OK   — {label}")
    except Exception as e:
        CHECKS_FAILED.append((label, e))
        print(f"  ECHEC — {label}")
        print(f"         {type(e).__name__}: {e}")


def check_imports():
    import pandas, openpyxl, rapidfuzz, streamlit, reportlab, docx  # noqa: F401
    import lxml, html5lib, bs4, pdfplumber, pytesseract, PIL  # noqa: F401


def check_tesseract_binary():
    import shutil
    if shutil.which("tesseract") is None:
        raise RuntimeError(
            "Le programme 'tesseract-ocr' n'est pas installé sur cette machine — "
            "la lecture de fichiers image (.jpeg/.png) ne fonctionnera pas. "
            "Voir la note dans requirements.txt pour l'installer."
        )


def check_full_pipeline():
    import pandas as pd
    from analysis.access_review import analyze_access
    from analysis.sod_detection import detect_sod_conflicts
    from reporting.export import generate_pdf_report, generate_word_report, generate_excel_report

    df = pd.DataFrame({
        "username": ["jdupont", "mmartin"], "system": ["AD", "AD"],
        "account_status": ["Active", "Active"],
        "last_login_date": ["2020-01-01", "2026-08-01"],
        "role": ["Payment Creator; Payment Validator", "Standard User"],
    })
    result = analyze_access(df, reference_datetime=pd.Timestamp.now())
    result = detect_sod_conflicts(result, conflicts=[("Payment Creator", "Payment Validator")])
    if not result.loc[0, "is_dormant"]:
        raise RuntimeError("Le contrôle de dormance n'a pas détecté un compte pourtant inactif depuis 2020.")
    if not result.loc[0, "sod_conflict"]:
        raise RuntimeError("Le contrôle SoD n'a pas détecté un conflit pourtant présent.")

    with tempfile.TemporaryDirectory() as tmp:
        generate_pdf_report(result, str(Path(tmp) / "test.pdf"))
        generate_word_report(result, str(Path(tmp) / "test.docx"))
        generate_excel_report(result, str(Path(tmp) / "test.xlsx"))
        for name in ("test.pdf", "test.docx", "test.xlsx"):
            path = Path(tmp) / name
            if not path.exists() or path.stat().st_size == 0:
                raise RuntimeError(f"Le rapport {name} n'a pas été généré correctement.")


def check_data_folder_writable():
    data_dir = Path(__file__).parent / "data"
    data_dir.mkdir(exist_ok=True)
    probe = data_dir / ".write_test"
    probe.write_text("test")
    probe.unlink()


def check_custom_mapping_roundtrip():
    import tempfile
    from ingestion.custom_column_mappings import save_custom_column_mapping, load_custom_column_mappings, forget_custom_column_mapping
    store = Path(tempfile.mktemp(suffix=".json"))
    save_custom_column_mapping("colonne_test", "username", store_path=store)
    if load_custom_column_mappings(store_path=store).get("colonne test") != "username":
        raise RuntimeError("La mémorisation d'une correspondance de colonne ne fonctionne pas.")
    forget_custom_column_mapping("colonne_test", store_path=store)


def main():
    print("=" * 70)
    print("Vérification de l'installation — Access Review Toolkit")
    print("=" * 70)
    print()
    print("1. Bibliothèques Python nécessaires installées")
    _check("Toutes les bibliothèques s'importent sans erreur", check_imports)
    print()
    print("2. Dépendance système (OCR d'images)")
    _check("Le programme tesseract-ocr est installé (facultatif : "
           "seule la lecture d'images en dépend)", check_tesseract_binary)
    print()
    print("3. Le dossier de données peut être écrit")
    _check("Le dossier 'data/' est accessible en écriture", check_data_folder_writable)
    print()
    print("4. Mémorisation des corrections de colonnes")
    _check("Une correspondance de colonne enregistrée est bien relue", check_custom_mapping_roundtrip)
    print()
    print("5. Chaîne complète : analyse + génération des 3 rapports")
    _check("Import, analyse (dormance, SoD) et génération PDF/Word/Excel", check_full_pipeline)
    print()
    print("=" * 70)
    if CHECKS_FAILED:
        print(f"RÉSULTAT : {len(CHECKS_FAILED)} vérification(s) en échec sur {len(CHECKS_PASSED) + len(CHECKS_FAILED)}.")
        print()
        print("À faire : partage le message d'erreur ci-dessus (nom de la vérification")
        print("et le message technique juste en dessous) avec la personne qui te")
        print("dépanne — c'est l'information la plus utile pour comprendre le problème.")
        print("Le tesseract-ocr manquant seul (vérification 2) n'empêche PAS d'utiliser")
        print("l'outil normalement — seule la lecture de fichiers image en dépend.")
        sys.exit(1)
    else:
        print("RÉSULTAT : tout fonctionne correctement. L'outil est prêt à l'emploi.")
        print("Tu peux lancer le dashboard avec : streamlit run dashboard/app.py")
        sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        print()
        print("Une erreur inattendue a interrompu la vérification elle-même :")
        traceback.print_exc()
        sys.exit(2)
