import os
import tempfile
import threading
import time
from pathlib import Path

from analysis.file_lock import locked


def test_concurrent_review_decisions_all_survive():
    """
    Vrai bug trouvé en poussant la fiabilité au maximum : sans verrou,
    20 décisions de revue enregistrées simultanément (scénario réaliste
    pour une équipe de plusieurs reviewers utilisant le dashboard en
    même temps) se réduisaient silencieusement à 1 seule survivante —
    perte de mise à jour classique (chacun lit avant l'écriture de
    l'autre), le fichier étant même parfois signalé corrompu si les
    écritures se chevauchaient physiquement.
    """
    from analysis.review_workflow import apply_review_decision, get_audit_trail

    store_path = Path(tempfile.gettempdir()) / f"test_concurrent_decisions_{time.time()}.json"

    def write_decision(i):
        apply_review_decision(f"user{i}", "AD", "Validé - accès légitime", f"reviewer{i}", "OK", store_path=store_path)

    threads = [threading.Thread(target=write_decision, args=(i,)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    for i in range(20):
        trail = get_audit_trail(f"user{i}", "AD", store_path=store_path)
        assert len(trail) == 1, f"Décision perdue pour user{i}"
    os.remove(store_path)
    print("OK - test_concurrent_review_decisions_all_survive")


def test_concurrent_trend_snapshots_all_survive():
    """Même protection appliquée à l'historique de tendance : 20
    enregistrements de cycle simultanés doivent tous survivre."""
    import pandas as pd
    from analysis.access_review import analyze_access
    from analysis.trend_tracking import record_cycle_snapshot, load_trend_history

    store_path = Path(tempfile.gettempdir()) / f"test_concurrent_trend_{time.time()}.json"
    df = analyze_access(pd.DataFrame({"username": ["u1"], "system": ["AD"]}))

    def write_snapshot(i):
        record_cycle_snapshot(df, f"Cycle {i}", store_path=store_path)

    threads = [threading.Thread(target=write_snapshot, args=(i,)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    history = load_trend_history(store_path=store_path)
    assert len(history) == 20
    os.remove(store_path)
    print("OK - test_concurrent_trend_snapshots_all_survive")


def test_stale_lock_is_recovered_not_blocked_forever():
    """Un verrou abandonné (ex. process précédent interrompu sans
    nettoyer) ne doit pas bloquer indéfiniment un usage légitime — il
    doit être retiré de force après le délai d'attente."""
    store_path = Path(tempfile.gettempdir()) / f"test_stale_lock_{time.time()}.json"
    stale_lock = Path(f"{store_path}.lock")
    stale_lock.parent.mkdir(parents=True, exist_ok=True)
    stale_lock.touch()  # verrou "abandonné", jamais retiré

    start = time.time()
    with locked(store_path, timeout=0.3, retry_interval=0.05):
        elapsed = time.time() - start
        assert elapsed < 2.0, "Le verrou abandonné aurait dû être récupéré rapidement"
    assert not stale_lock.exists()
    print("OK - test_stale_lock_is_recovered_not_blocked_forever")
