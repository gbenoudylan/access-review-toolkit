import tempfile
from pathlib import Path

import pandas as pd

from analysis.access_review import analyze_access
from analysis.trend_tracking import (
    record_cycle_snapshot, load_trend_history, scope_changed_between, _load_store,
)


def _fresh_store():
    path = Path(tempfile.gettempdir()) / f"test_trend_{id(object())}.json"
    if path.exists():
        path.unlink()
    return path


def test_record_and_load_single_cycle():
    """Un instantané enregistré doit se retrouver intégralement dans
    l'historique chargé, avec le périmètre et les indicateurs corrects."""
    store_path = _fresh_store()
    df = pd.DataFrame({
        "username": ["u1", "u2"], "system": ["AD"] * 2,
        "account_status": ["Active"] * 2, "last_login_date": ["2020-01-01"] * 2,
    })
    record_cycle_snapshot(analyze_access(df), "T1 2026", store_path=store_path)
    history = load_trend_history(store_path=store_path)
    assert len(history) == 1
    assert history.loc[0, "period_label"] == "T1 2026"
    assert history.loc[0, "systems"] == "AD"
    assert history.loc[0, "total_accounts"] == 2
    assert history.loc[0, "is_dormant"] == 2
    store_path.unlink()
    print("OK - test_record_and_load_single_cycle")


def test_scope_filtered_trend_stays_comparable_across_scope_changes():
    """
    Demande explicite : deux cycles peuvent ne pas couvrir les mêmes
    systèmes (ex. SAP ajouté au périmètre entre deux revues). Filtrer
    l'historique sur un système donné doit retourner les indicateurs
    calculés SPÉCIFIQUEMENT pour ce système à chaque cycle, pas les
    totaux globaux du cycle (qui incluraient à tort les autres
    systèmes) — sans quoi la comparaison resterait trompeuse malgré le
    filtre.
    """
    store_path = _fresh_store()
    df1 = pd.DataFrame({
        "username": ["u1", "u2", "u3"], "system": ["AD"] * 3,
        "account_status": ["Active"] * 3, "last_login_date": ["2020-01-01"] * 3,
    })
    record_cycle_snapshot(analyze_access(df1), "T1 2026", store_path=store_path)

    df2 = pd.DataFrame({
        "username": ["u1", "u2", "u3", "u4"], "system": ["AD", "AD", "AD", "SAP"],
        "account_status": ["Active"] * 4, "last_login_date": ["2020-01-01"] * 4,
    })
    record_cycle_snapshot(analyze_access(df2), "T2 2026", store_path=store_path)

    ad_only = load_trend_history(store_path=store_path, system="AD")
    assert len(ad_only) == 2
    # Les deux cycles doivent montrer EXACTEMENT le même sous-total AD
    # (3 comptes, 3 dormants), peu importe que SAP ait été ajouté au
    # cycle 2 — sinon la comparaison "AD seul" serait faussée par SAP.
    assert ad_only["total_accounts"].tolist() == [3, 3]
    assert ad_only["is_dormant"].tolist() == [3, 3]
    store_path.unlink()
    print("OK - test_scope_filtered_trend_stays_comparable_across_scope_changes")


def test_scope_changed_between_detects_real_scope_changes():
    """Le changement de périmètre entre deux instantanés consécutifs
    doit être détecté, pour être signalé plutôt que silencieusement
    mélangé à une vraie évolution des indicateurs."""
    store_path = _fresh_store()
    df1 = pd.DataFrame({"username": ["u1"], "system": ["AD"], "account_status": ["Active"]})
    snap1 = record_cycle_snapshot(analyze_access(df1), "T1", store_path=store_path)

    df2 = pd.DataFrame({"username": ["u1"], "system": ["AD"], "account_status": ["Active"]})
    snap2 = record_cycle_snapshot(analyze_access(df2), "T2", store_path=store_path)

    df3 = pd.DataFrame({
        "username": ["u1", "u2"], "system": ["AD", "SAP"], "account_status": ["Active"] * 2,
    })
    snap3 = record_cycle_snapshot(analyze_access(df3), "T3", store_path=store_path)

    assert scope_changed_between(snap1, snap2) is False
    assert scope_changed_between(snap2, snap3) is True
    store_path.unlink()
    print("OK - test_scope_changed_between_detects_real_scope_changes")


def test_corrupted_trend_history_file_restarts_cleanly():
    """Un fichier d'historique corrompu ne doit pas faire planter
    l'enregistrement d'un nouveau cycle — redémarrage propre plutôt
    qu'un crash, cohérent avec le comportement déjà établi pour
    l'audit trail des décisions de revue."""
    store_path = _fresh_store()
    with open(store_path, "w") as f:
        f.write("{ceci n'est pas du json valide")

    df = pd.DataFrame({"username": ["u1"], "system": ["AD"], "account_status": ["Active"]})
    record_cycle_snapshot(analyze_access(df), "T1", store_path=store_path)
    history = load_trend_history(store_path=store_path)
    assert len(history) == 1
    store_path.unlink()
    print("OK - test_corrupted_trend_history_file_restarts_cleanly")


def test_empty_history_returns_empty_dataframe_not_error():
    """Charger un historique qui n'existe pas encore doit retourner un
    DataFrame vide exploitable, pas une erreur — cas normal la toute
    première fois que la fonctionnalité est utilisée."""
    store_path = Path(tempfile.gettempdir()) / "test_trend_never_existed.json"
    if store_path.exists():
        store_path.unlink()
    history = load_trend_history(store_path=store_path)
    assert history.empty
    print("OK - test_empty_history_returns_empty_dataframe_not_error")


def test_multiple_cycles_same_system_show_real_trend():
    """Vérification de bout en bout : trois cycles sur le même
    périmètre, avec une vraie amélioration (moins de comptes dormants),
    doivent apparaître dans l'ordre chronologique avec les bons
    chiffres — le scénario que cette fonctionnalité doit servir."""
    store_path = _fresh_store()
    for i, n_dormant in enumerate([5, 3, 1]):
        n_active = 10 - n_dormant
        dates = ["2020-01-01"] * n_dormant + ["2026-09-01"] * n_active
        df = pd.DataFrame({
            "username": [f"u{j}" for j in range(10)], "system": ["AD"] * 10,
            "account_status": ["Active"] * 10, "last_login_date": dates,
        })
        record_cycle_snapshot(analyze_access(df), f"Cycle {i+1}", store_path=store_path)

    history = load_trend_history(store_path=store_path)
    assert history["is_dormant"].tolist() == [5, 3, 1]
    store_path.unlink()
    print("OK - test_multiple_cycles_same_system_show_real_trend")


def test_system_filter_is_case_insensitive_across_cycles():
    """
    Vrai bug trouvé en poussant la fiabilité au maximum : le même
    système peut être enregistré avec une casse différente selon le
    cycle (variation d'export réaliste, ex. 'AD' puis 'ad') — filtrer
    l'historique sur 'AD' ratait silencieusement les cycles enregistrés
    autrement, donnant l'impression trompeuse que la tendance s'était
    arrêtée alors que rien n'avait changé sur le fond.
    """
    store_path = _fresh_store()
    df1 = pd.DataFrame({"username": ["u1", "u2"], "system": ["AD", "AD"], "account_status": ["Active"] * 2})
    record_cycle_snapshot(analyze_access(df1), "T1", store_path=store_path)
    df2 = pd.DataFrame({"username": ["u1", "u2"], "system": ["ad", "ad"], "account_status": ["Active"] * 2})
    record_cycle_snapshot(analyze_access(df2), "T2", store_path=store_path)

    result = load_trend_history(store_path=store_path, system="AD")
    assert len(result) == 2
    assert result["total_accounts"].tolist() == [2, 2]
    store_path.unlink()
    print("OK - test_system_filter_is_case_insensitive_across_cycles")


def test_orphaned_accounts_are_tracked_in_trend_history():
    store_path = _fresh_store()
    df = pd.DataFrame({
        "username": ["admin_ci", "jdupont"], "full_name": ["", "Jean Dupont"],
        "system": ["BSS", "BSS"], "account_status": ["Active", "Active"],
    })
    record_cycle_snapshot(analyze_access(df), "T1", store_path=store_path)
    record_cycle_snapshot(analyze_access(df), "T2", store_path=store_path)
    history = load_trend_history(store_path=store_path)
    assert history["is_orphaned_account"].tolist() == [1, 1]
    store_path.unlink()
    print("OK - test_orphaned_accounts_are_tracked_in_trend_history")
