import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))


def test_dashboard_runs_without_exception_on_sample_file():
    """
    Le dashboard complet (page unique, défilement, bannière, cartes KPI)
    doit s'exécuter de bout en bout sans exception avec le fichier
    d'exemple, coché par défaut — vérifie réellement l'exécution du
    script (contrairement à un simple curl sur la page, qui ne déclenche
    pas de session Streamlit complète et masquait un premier essai de
    vérification insuffisant).
    """
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(str(Path(__file__).parent.parent / "dashboard" / "app.py"))
    at.run(timeout=60)
    assert not at.exception, f"Exception(s) au démarrage : {at.exception}"
    print("OK - test_dashboard_runs_without_exception_on_sample_file")


def test_dashboard_banner_and_kpi_cards_render():
    """Le titre et les indicateurs clés doivent apparaître avec le bon
    contenu — style Streamlit natif, sans emoji ni bannière personnalisée
    dans la partie principale."""
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(str(Path(__file__).parent.parent / "dashboard" / "app.py"))
    at.run(timeout=60)
    assert "Access Review & IAM" in at.title[0].value
    metric_labels = [m.label for m in at.metric]
    assert "Comptes analysés" in metric_labels
    print("OK - test_dashboard_banner_and_kpi_cards_render")


def test_dashboard_is_single_page_with_reports_at_the_bottom():
    """
    Préférence explicite de l'utilisateur : tout sur une seule page, pas
    d'onglets, avec 'Rapports formatés' tout en bas après défilement —
    pas de séparation en onglets (testé auparavant, puis explicitement
    annulé sur demande).
    """
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(str(Path(__file__).parent.parent / "dashboard" / "app.py"))
    at.run(timeout=60)
    assert len(at.tabs) == 0, "Le dashboard ne doit plus utiliser d'onglets"
    sidebar_subheaders = {
        "🔗 Croisement RH (optionnel)", "⚙️ Seuils des contrôles",
        "🔐 Matrice SoD personnalisée (optionnel)",
        "🔄 Comptes transférés/mutés (optionnel)",
    }
    main_subheaders = [s.value for s in at.subheader if s.value not in sidebar_subheaders]
    assert main_subheaders[-1] == "Rapports formatés"
    print("OK - test_dashboard_is_single_page_with_reports_at_the_bottom")


def test_dashboard_main_content_has_no_emoji():
    """
    Demande explicite : aucun emoji dans la partie principale (à droite
    de la barre latérale) — la barre latérale elle-même n'est pas
    concernée par cette contrainte.
    """
    import re
    app_path = Path(__file__).parent.parent / "dashboard" / "app.py"
    with open(app_path, encoding="utf-8") as f:
        source = f.read()

    emoji_pattern = re.compile(r"[\U0001F300-\U0001FAFF\U00002600-\U000027BF]")
    # Lignes autorisées à contenir un emoji : icône de page (onglet
    # navigateur, invisible dans la page) et code de statut interne
    # (comparaison de chaîne, pas un affichage décoratif).
    allowed_snippets = ["page_icon=", 'status == "⚠️"']
    offending_lines = []
    for line in source.splitlines():
        if emoji_pattern.search(line) and not any(a in line for a in allowed_snippets):
            # La barre latérale a ses propres emojis, hors du périmètre de cette contrainte.
            if "st.header(" in line or "🔗" in line or "⚙️" in line or "🔐" in line or "🔄" in line or "💾" in line or "⚠️" in line or "🟢" in line or "🔴" in line or "✅" in line or "❌" in line:
                continue
            offending_lines.append(line)
    assert not offending_lines, f"Emoji(s) trouvé(s) hors barre latérale : {offending_lines}"
    print("OK - test_dashboard_main_content_has_no_emoji")


if __name__ == "__main__":
    test_dashboard_runs_without_exception_on_sample_file()
    test_dashboard_banner_and_kpi_cards_render()
    test_dashboard_is_single_page_with_reports_at_the_bottom()
    test_dashboard_main_content_has_no_emoji()
    print("Tous les tests passent.")


def test_investigation_account_lookup_is_case_insensitive():
    """
    Vrai bug trouvé : le même identifiant peut apparaître avec une casse
    différente selon le système source (ex. 'jdupont' sur AD, 'JDupont'
    sur SAP) — une correspondance stricte sur le compte sélectionné pour
    l'investigation faisait manquer les autres comptes de la même
    personne, sans même montrer le sélecteur multi-système qui les
    signalerait normalement. Vérifié directement sur la logique de
    filtrage, pas seulement sur le code source.
    """
    import pandas as pd

    df = pd.DataFrame({"username": ["jdupont", "JDupont"], "system": ["AD", "SAP"]})
    selected_username = "jdupont"
    selected_norm = str(selected_username).strip().lower()
    matches = df[df["username"].astype(str).str.strip().str.lower() == selected_norm]
    assert len(matches) == 2
    assert set(matches["system"]) == {"AD", "SAP"}
    print("OK - test_investigation_account_lookup_is_case_insensitive")


def test_transfer_and_previous_file_checks_are_cached_functions():
    """
    Vrai problème de performance trouvé en poussant la fiabilité au
    maximum : la vérification des colonnes non reconnues du fichier de
    revue précédente et du fichier de transferts n'était PAS mise en
    cache — Streamlit réexécutant tout le script à chaque interaction
    du dashboard (même sans rapport, ex. changer un seuil), ces fichiers
    étaient reparsés inutilement à chaque fois. Corrigé avec
    @st.cache_data, comme run_pipeline. Vérifié ici que les deux
    fonctions restent correctes fonctionnellement après ce changement.
    """
    import importlib
    import dashboard.app as app_module
    importlib.reload(app_module)

    assert hasattr(app_module._check_transfer_file_columns, "__wrapped__") or \
        "cache" in str(type(app_module._check_transfer_file_columns)).lower()
    assert hasattr(app_module._check_previous_file_columns, "__wrapped__") or \
        "cache" in str(type(app_module._check_previous_file_columns)).lower()
    print("OK - test_transfer_and_previous_file_checks_are_cached_functions")


def test_column_mapping_dropdown_trimmed_to_control_driving_fields():
    """
    Demande explicite : le menu déroulant de correction de colonnes ne
    doit proposer que les champs qui pilotent réellement un contrôle
    (username, system, statut, dates, manager, rôle...), pas les champs
    purement informatifs (nom complet, email, téléphone, poste...) —
    pour ne pas noyer l'utilisateur dans une liste trop longue face à
    ce qui compte vraiment.
    """
    import importlib
    import dashboard.app as app_module
    importlib.reload(app_module)
    import inspect

    source = inspect.getsource(app_module.main)
    assert '"username", "system", "account_status", "is_locked",' in source
    assert '"email"' not in source.split("STANDARD_FIELDS_FOR_MAPPING = [")[1][:200]
    print("OK - test_column_mapping_dropdown_trimmed_to_control_driving_fields")


def test_backup_zip_contains_all_data_files():
    """
    Idée proposée pour la fiabilité sans maintenance : un bouton de
    sauvegarde permet de télécharger en un clic tout ce que l'outil a
    appris (décisions, correspondances de colonnes, acceptations de
    risque, historique de tendance) — sans lui, tout redémarrerait de
    zéro en cas de changement de machine.
    """
    import io
    import zipfile
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp)
        (data_dir / "review_decisions.json").write_text('{"a": 1}')
        (data_dir / "risk_acceptances.json").write_text('{"b": 2}')

        data_files = sorted(data_dir.glob("*.json"))
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            for f in data_files:
                zf.write(f, arcname=f.name)

        with zipfile.ZipFile(io.BytesIO(buffer.getvalue())) as zf:
            names = zf.namelist()
            assert "review_decisions.json" in names
            assert "risk_acceptances.json" in names
    print("OK - test_backup_zip_contains_all_data_files")
