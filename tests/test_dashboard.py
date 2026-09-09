import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))


def test_dashboard_runs_without_exception_on_sample_file():
    """
    Le dashboard complet (5 onglets, bannière, cartes KPI) doit s'exécuter
    de bout en bout sans exception avec le fichier d'exemple, coché par
    défaut — vérifie réellement l'exécution du script (contrairement à un
    simple curl sur la page, qui ne déclenche pas de session Streamlit
    complète et masquait un premier essai de vérification insuffisant).
    """
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(str(Path(__file__).parent.parent / "dashboard" / "app.py"))
    at.run(timeout=60)
    assert not at.exception, f"Exception(s) au démarrage : {at.exception}"
    assert len(at.tabs) == 5
    print("OK - test_dashboard_runs_without_exception_on_sample_file")


def test_dashboard_banner_and_kpi_cards_render():
    """La bannière personnalisée et les cartes KPI doivent apparaître
    avec le bon contenu — vérifie que la réorganisation visuelle n'a
    rien cassé dans le contenu affiché."""
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(str(Path(__file__).parent.parent / "dashboard" / "app.py"))
    at.run(timeout=60)
    all_markdown = "\n".join(m.value for m in at.markdown)
    assert "Access Review & IAM" in all_markdown
    assert "Comptes analysés" in all_markdown
    print("OK - test_dashboard_banner_and_kpi_cards_render")


if __name__ == "__main__":
    test_dashboard_runs_without_exception_on_sample_file()
    test_dashboard_banner_and_kpi_cards_render()
    print("Tous les tests passent.")
