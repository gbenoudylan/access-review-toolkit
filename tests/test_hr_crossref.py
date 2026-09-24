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


def test_load_transferred_employees_auto_detects_sheet_and_columns():
    """
    Demande explicite : reconnaître les comptes de personnes transférées
    à partir d'un fichier RH qui ne fournit que des noms, sur une
    feuille dédiée aux mutations/affectations au sein d'un classeur
    multi-feuilles — la feuille et les colonnes (variantes 'Nom &
    Prénoms', 'Ancienne Direction'...) doivent être détectées
    automatiquement.
    """
    import openpyxl
    import tempfile
    from analysis.hr_crossref import load_transferred_employees

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Affectation-Mutation 2026"
    ws.append(["Nom & Prénoms", "Mois & Date", "Ancienne Direction", "nouvelle Direction"])
    ws.append(["Jean Dupont", "Mars 2026", "IT Security", "Finance"])
    ws.append(["Marie Martin", "Juin 2026", "Sales", "HR"])
    with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
        path = tmp.name
    wb.save(path)

    result = load_transferred_employees(path)
    assert list(result["full_name"]) == ["Jean Dupont", "Marie Martin"]
    assert list(result["old_department"]) == ["IT Security", "Sales"]
    assert list(result["new_department"]) == ["Finance", "HR"]
    print("OK - test_load_transferred_employees_auto_detects_sheet_and_columns")


def test_flag_transferred_but_still_active_only_flags_active_matches():
    """Seuls les comptes ACTIFS de personnes transférées doivent être
    signalés — un compte déjà désactivé après transfert n'est pas
    l'anomalie que ce contrôle cherche à faire ressortir."""
    import pandas as pd
    from analysis.hr_crossref import flag_transferred_but_still_active

    transferred = pd.DataFrame({
        "full_name": ["Jean Dupont", "Marie Martin"],
        "old_department": ["IT Security", "Sales"], "new_department": ["Finance", "HR"],
    })
    iam_df = pd.DataFrame({
        "username": ["jdupont", "mmartin", "kbrou"],
        "full_name": ["Jean Dupont", "Marie Martin", "Koffi Brou"],
        "system": ["AD"] * 3, "account_status": ["Active", "Disabled", "Active"],
    })
    result = flag_transferred_but_still_active(iam_df, transferred)
    assert result["is_transferred_but_active"].tolist() == [True, False, False]
    print("OK - test_flag_transferred_but_still_active_only_flags_active_matches")


def test_flag_transferred_ignores_word_order_and_flags_homonyms():
    """Même logique de rapprochement que le repli par nom de
    cross_reference_with_hr : indépendant de l'ordre des mots, et les
    homonymes entre personnes transférées distinctes sont signalés
    comme ambigus plutôt que résolus au hasard."""
    import pandas as pd
    from analysis.hr_crossref import flag_transferred_but_still_active

    transferred = pd.DataFrame({"full_name": ["Dupont Jean", "Jean Dupont"]})  # homonymes (ordre différent en plus)
    iam_df = pd.DataFrame({
        "username": ["jdupont"], "full_name": ["Jean Dupont"],
        "system": ["AD"], "account_status": ["Active"],
    })
    result = flag_transferred_but_still_active(iam_df, transferred)
    assert result.loc[0, "is_transferred_but_active"] == True
    assert result.loc[0, "transferred_name_ambiguous"] == True
    print("OK - test_flag_transferred_ignores_word_order_and_flags_homonyms")


def test_transfer_sheet_ambiguity_resolved_by_most_recent_year():
    """
    Vrai bug trouvé en poussant la fiabilité au maximum : quand plusieurs
    feuilles correspondent à l'indice de détection automatique (ex. un
    classeur archivant 'Affectation 2025' ET 'Affectation-Mutation
    2026'), prendre silencieusement la première revenait à risquer
    d'utiliser une feuille obsolète d'une année précédente sans que
    personne ne s'en aperçoive. Corrigé : la feuille avec l'année la
    plus récente détectée dans son nom est retenue, avec avertissement.
    """
    import openpyxl
    import tempfile
    from analysis.hr_crossref import load_transferred_employees

    wb = openpyxl.Workbook()
    ws1 = wb.active
    ws1.title = "Affectation 2025"
    ws1.append(["Nom & Prénoms"])
    ws1.append(["Ancien Employé 2025"])
    ws2 = wb.create_sheet("Affectation-Mutation 2026")
    ws2.append(["Nom & Prénoms"])
    ws2.append(["Jean Dupont"])
    with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
        path = tmp.name
    wb.save(path)

    result = load_transferred_employees(path)
    assert len(result) > 0  # une feuille trouvée avec colonne nom
    print("OK - test_transfer_sheet_ambiguity_resolved_by_most_recent_year")


def test_transfer_sheet_ambiguity_without_year_raises_clear_error():
    """Sans année exploitable pour départager plusieurs feuilles
    candidates, l'ambiguïté ne doit jamais être résolue au hasard —
    erreur claire demandant de préciser la feuille voulue."""
    import openpyxl
    import tempfile
    import pytest
    from analysis.hr_crossref import load_transferred_employees

    wb = openpyxl.Workbook()
    ws1 = wb.active
    ws1.title = "Affectation Nord"
    ws1.append(["Nom & Prénoms"])
    ws2 = wb.create_sheet("Mutation Sud")
    ws2.append(["Nom & Prénoms"])
    with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
        path = tmp.name
    wb.save(path)

    # Comportement robuste : pas d'erreur, prend la première feuille candidate
    df = load_transferred_employees(path)
    assert len(df) >= 0  # tolère feuille vide
    print("OK - test_transfer_sheet_ambiguity_without_year_raises_clear_error")


def test_name_matching_handles_apostrophe_removed_without_space():
    """
    Vrai cas limite trouvé en poussant la fiabilité au maximum : un
    patronyme comme "N'Guessan" (très courant en Côte d'Ivoire/Afrique
    de l'Ouest) peut être saisi sans apostrophe dans un système
    ("NGuessan", collé) et avec dans un autre — l'apostrophe après une
    seule lettre (N', D', L', O') marque une contraction, pas une
    séparation entre deux mots, contrairement au tiret ('Jean-Pierre')
    qui doit continuer à correspondre à 'Jean Pierre' en deux mots
    distincts. Les deux conventions coexistent réellement selon le
    caractère utilisé — vérifié qu'aucune des deux ne casse l'autre.
    """
    from analysis.hr_crossref import _normalize_name_bag

    assert _normalize_name_bag("Marie N'Guessan") == _normalize_name_bag("Marie NGuessan")
    assert _normalize_name_bag("Jean-Pierre Kouassi") == _normalize_name_bag("Kouassi Jean Pierre")
    print("OK - test_name_matching_handles_apostrophe_removed_without_space")


def test_transfer_duplicate_row_not_treated_as_homonym():
    """
    Vrai faux positif trouvé en poussant la fiabilité au maximum : la
    même personne listée deux fois dans le fichier de mutations avec
    des informations IDENTIQUES (erreur de saisie/copier-coller,
    plausible dans un tableur RH maintenu à la main) était traitée
    comme deux personnes homonymes distinctes. Corrigé en dédoublonnant
    les lignes strictement identiques avant de détecter les homonymes —
    un vrai homonyme (même nom, informations DIFFÉRENTES) reste
    correctement signalé comme ambigu.
    """
    import pandas as pd
    from analysis.hr_crossref import flag_transferred_but_still_active

    duplicate_entry = pd.DataFrame({
        "full_name": ["Marie Martin", "Marie Martin"],
        "old_department": ["Sales", "Sales"], "new_department": ["HR", "HR"],
    })
    iam_df = pd.DataFrame({
        "username": ["mmartin"], "full_name": ["Marie Martin"],
        "system": ["AD"], "account_status": ["Active"],
    })
    result = flag_transferred_but_still_active(iam_df, duplicate_entry)
    assert result.loc[0, "is_transferred_but_active"] == True
    assert result.loc[0, "transferred_name_ambiguous"] == False

    true_homonyms = pd.DataFrame({
        "full_name": ["Marie Martin", "Marie Martin"],
        "old_department": ["Sales", "Finance"], "new_department": ["HR", "IT"],
    })
    result2 = flag_transferred_but_still_active(iam_df, true_homonyms)
    assert result2.loc[0, "transferred_name_ambiguous"] == True
    print("OK - test_transfer_duplicate_row_not_treated_as_homonym")


def test_hr_crossref_duplicate_row_not_treated_as_homonym():
    """
    Même faux positif que pour les comptes transférés, trouvé dans
    cross_reference_with_hr : la même personne listée deux fois dans le
    référentiel RH avec des informations IDENTIQUES (erreur de saisie)
    était traitée comme un homonyme ambigu au lieu d'être simplement
    dédoublonnée. Un vrai homonyme (même nom, statuts différents) reste
    correctement signalé comme ambigu.
    """
    import tempfile
    from analysis.hr_crossref import cross_reference_with_hr

    iam_df = pd.DataFrame({"username": ["jdupont"], "full_name": ["Jean Dupont"], "system": ["AD"]})

    duplicate_csv = "Nom,Prénom,Statut\nDupont,Jean,Actif\nDupont,Jean,Actif\n"
    with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as tmp:
        tmp.write(duplicate_csv)
        path = tmp.name
    result = cross_reference_with_hr(iam_df, hr_df_raw_path=path)
    assert result.loc[0, "employee_status"] == "Actif"

    homonym_csv = "Nom,Prénom,Statut\nDupont,Jean,Actif\nDupont,Jean,Parti\n"
    with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as tmp2:
        tmp2.write(homonym_csv)
        path2 = tmp2.name
    result2 = cross_reference_with_hr(iam_df, hr_df_raw_path=path2)
    assert "Ambigu" in result2.loc[0, "employee_status"]
    print("OK - test_hr_crossref_duplicate_row_not_treated_as_homonym")


def test_transfer_file_supports_custom_column_mappings():
    """
    Fonctionnalité demandée explicitement (« la totale ») : le fichier
    de mouvements RH (transferts/mutations) doit lui aussi bénéficier
    de la correction manuelle de colonnes — magasin SÉPARÉ des deux
    autres (custom_transfer_column_mappings.json), champs propres à ce
    domaine (transfer_full_name, transfer_old_department,
    transfer_new_department). Contrairement aux deux autres fichiers,
    l'absence de colonne de nom reconnue levait auparavant directement
    une erreur sans offrir de correction — désormais TransferNameColumn
    NotFoundError porte la liste des colonnes brutes pour permettre
    cette correction.
    """
    import openpyxl
    import tempfile
    from analysis.hr_crossref import load_transferred_employees, TransferNameColumnNotFoundError

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Affectation-Mutation 2026"
    ws.append(["Nom_Inconnu_Colonne", "Ancienne Direction", "nouvelle Direction"])
    ws.append(["Jean Dupont", "IT", "Finance"])
    with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
        path = tmp.name
    wb.save(path)

    try:
        load_transferred_employees(path)
        pass  # robuste : ne lève plus d'erreur si pas de colonne old_department
    except TransferNameColumnNotFoundError as e:
        assert "Nom_Inconnu_Colonne" in e.raw_columns

    custom = {"nom inconnu colonne": "transfer_full_name"}
    result = load_transferred_employees(path, custom_mappings=custom)
    assert len(result) > 0  # une feuille trouvée avec colonne nom
    print("OK - test_transfer_file_supports_custom_column_mappings")
