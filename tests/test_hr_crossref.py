import pandas as pd
from analysis.hr_crossref import cross_reference_with_hr


def test_hr_crossref_normalizes_case_for_matching():
    """
    Vrai bug trouvé : IAM et RH sont deux systèmes distincts, maintenus
    par des équipes différentes, avec des conventions de casse
    potentiellement différentes ('jdupont' côté annuaire, 'JDupont'
    côté SIRH) — le rapprochement se faisait sur une correspondance
    exacte, sans normalisation. Une personne réellement employée et
    présente dans les deux systèmes était marquée à tort "absente du
    référentiel RH", un faux positif sérieux pour un contrôle de
    sécurité.
    """
    iam_df = pd.DataFrame({"username": ["jdupont", "mmartin"], "system": ["AD"] * 2})
    hr_df = pd.DataFrame({
        "hr_username": ["JDupont", "mmartin"],
        "hr_employee_status": ["Active", "Terminated"],
        "hr_department": ["IT", "Finance"],
    })
    result = cross_reference_with_hr(iam_df, hr_df=hr_df)
    assert result.loc[0, "employee_status"] == "Active"
    assert result.loc[0, "department"] == "IT"
    assert result.loc[1, "employee_status"] == "Terminated"
    print("OK - test_hr_crossref_normalizes_case_for_matching")


def test_hr_crossref_still_flags_genuinely_unmatched_account():
    """Un compte réellement absent du référentiel RH doit rester
    signalé comme tel — la normalisation ne doit pas créer de faux
    rapprochement."""
    iam_df = pd.DataFrame({"username": ["ghost_account"], "system": ["AD"]})
    hr_df = pd.DataFrame({"hr_username": ["jdupont"], "hr_employee_status": ["Active"]})
    result = cross_reference_with_hr(iam_df, hr_df=hr_df)
    assert result.loc[0, "employee_status"] == "Inconnu (absent du référentiel RH)"
    print("OK - test_hr_crossref_still_flags_genuinely_unmatched_account")


def test_hr_crossref_normalizes_whitespace_too():
    """Des espaces parasites (' jdupont ' côté RH, fréquents dans un
    export Excel) ne doivent pas non plus empêcher le rapprochement."""
    iam_df = pd.DataFrame({"username": ["jdupont"], "system": ["AD"]})
    hr_df = pd.DataFrame({"hr_username": [" jdupont "], "hr_employee_status": ["Active"]})
    result = cross_reference_with_hr(iam_df, hr_df=hr_df)
    assert result.loc[0, "employee_status"] == "Active"
    print("OK - test_hr_crossref_normalizes_whitespace_too")


def test_hr_crossref_falls_back_to_name_matching_when_no_id():
    """
    Cas réel très courant : le SIRH ne fournit que Nom/Prénom, aucun
    identifiant technique partagé avec l'IAM. Le rapprochement doit se
    replier sur le nom complet plutôt que de planter (KeyError brut,
    comportement original avant correction).
    """
    import tempfile
    from analysis.hr_crossref import cross_reference_with_hr

    iam_df = pd.DataFrame({
        "username": ["jdupont", "mmartin"], "full_name": ["Jean Dupont", "Marie Martin"],
        "system": ["AD"] * 2,
    })
    hr_csv = "Nom,Prénom,Statut\nDupont,Jean,Actif\nMartin,Marie,Parti\n"
    with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as tmp:
        tmp.write(hr_csv)
        path = tmp.name
    result = cross_reference_with_hr(iam_df, hr_df_raw_path=path)
    assert result.loc[0, "employee_status"] == "Actif"
    assert result.loc[1, "employee_status"] == "Parti"
    print("OK - test_hr_crossref_falls_back_to_name_matching_when_no_id")


def test_hr_crossref_name_matching_ignores_word_order():
    """Le rapprochement par nom doit être indépendant de l'ordre des
    mots ('Jean Dupont' côté IAM doit correspondre à 'Dupont Jean' côté
    RH) — aucune convention d'ordre n'est garantie identique entre deux
    systèmes distincts."""
    import tempfile
    from analysis.hr_crossref import cross_reference_with_hr

    iam_df = pd.DataFrame({"username": ["jdupont"], "full_name": ["Jean Dupont"], "system": ["AD"]})
    hr_csv = "Nom Complet,Statut\nDupont Jean,Actif\n"
    with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as tmp:
        tmp.write(hr_csv)
        path = tmp.name
    result = cross_reference_with_hr(iam_df, hr_df_raw_path=path)
    assert result.loc[0, "employee_status"] == "Actif"
    print("OK - test_hr_crossref_name_matching_ignores_word_order")


def test_hr_crossref_flags_homonyms_as_ambiguous_not_silently_resolved():
    """
    Deux employés RH portant le même nom (homonymes réels, plausibles
    dans une grande entreprise) ne doivent PAS être résolus au hasard en
    gardant le premier — marqués explicitement 'Ambigu' pour signaler
    l'incertitude plutôt que produire un résultat qui pourrait être
    faux la moitié du temps.
    """
    import tempfile
    from analysis.hr_crossref import cross_reference_with_hr

    iam_df = pd.DataFrame({"username": ["jdupont1"], "full_name": ["Jean Dupont"], "system": ["AD"]})
    hr_csv = "Nom,Prénom,Statut\nDupont,Jean,Actif\nDupont,Jean,Parti\n"
    with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as tmp:
        tmp.write(hr_csv)
        path = tmp.name
    result = cross_reference_with_hr(iam_df, hr_df_raw_path=path)
    assert "Ambigu" in result.loc[0, "employee_status"]
    print("OK - test_hr_crossref_flags_homonyms_as_ambiguous_not_silently_resolved")


def test_hr_crossref_raises_clear_error_when_nothing_exploitable():
    """Si le fichier RH n'a ni identifiant ni nom exploitable, l'erreur
    doit être claire et actionnable, pas un KeyError brut."""
    import tempfile
    import pytest
    from analysis.hr_crossref import cross_reference_with_hr

    iam_df = pd.DataFrame({"username": ["jdupont"], "full_name": ["Jean Dupont"], "system": ["AD"]})
    hr_csv = "Statut,Departement\nActif,IT\n"
    with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as tmp:
        tmp.write(hr_csv)
        path = tmp.name
    with pytest.raises(ValueError, match="ni identifiant exploitable"):
        cross_reference_with_hr(iam_df, hr_df_raw_path=path)
    print("OK - test_hr_crossref_raises_clear_error_when_nothing_exploitable")


def test_hr_crossref_prefers_username_over_name_when_both_available():
    """Quand le fichier RH fournit À LA FOIS un identifiant ET un nom,
    l'identifiant (plus fiable) doit être utilisé en priorité, pas le
    nom."""
    import tempfile
    from analysis.hr_crossref import cross_reference_with_hr

    iam_df = pd.DataFrame({"username": ["jdupont"], "full_name": ["Jean Dupont"], "system": ["AD"]})
    hr_csv = "Matricule,Nom,Prénom,Statut\njdupont,Dupont,Jean,Actif\n"
    with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as tmp:
        tmp.write(hr_csv)
        path = tmp.name
    result = cross_reference_with_hr(iam_df, hr_df_raw_path=path)
    assert result.loc[0, "employee_status"] == "Actif"
    print("OK - test_hr_crossref_prefers_username_over_name_when_both_available")
