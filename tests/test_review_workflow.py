import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import tempfile
import json
import os

import pandas as pd

from analysis.review_workflow import apply_review_decision, get_audit_trail, attach_review_status


def test_multiple_decisions_are_appended_not_overwritten():
    """
    Régression réelle : chaque nouvelle décision écrasait la précédente,
    rendant impossible de répondre à 'qui a validé quoi, quand, et
    pourquoi' si une décision changeait ensuite (ex. Révoqué après un
    premier Validé). Doit désormais empiler l'historique complet.
    """
    store_path = tempfile.mktemp(suffix=".json")
    apply_review_decision("user1", "AD", "En attente", store_path=store_path)
    apply_review_decision("user1", "AD", "Validé - accès légitime", "reviewer01", "OK", store_path)
    apply_review_decision("user1", "AD", "Révoqué", "reviewer02", "Finalement parti", store_path)

    history = get_audit_trail("user1", "AD", store_path)
    assert len(history) == 3
    assert history[0]["status"] == "En attente"
    assert history[-1]["status"] == "Révoqué"
    assert history[-1]["validated_by"] == "reviewer02"
    os.remove(store_path)
    print("OK - test_multiple_decisions_are_appended_not_overwritten")


def test_old_single_dict_format_migrated_transparently():
    """Un fichier de décisions dans l'ancien format (un seul dict par
    compte, sans historique) doit être migré silencieusement vers une
    liste, sans perdre la décision déjà enregistrée."""
    store_path = tempfile.mktemp(suffix=".json")
    with open(store_path, "w") as f:
        json.dump({"user1::AD": {"status": "En attente", "validated_by": "", "comment": "", "date": "2026-01-01 10:00"}}, f)

    apply_review_decision("user1", "AD", "Révoqué", "reviewer01", "Test", store_path)
    history = get_audit_trail("user1", "AD", store_path)
    assert len(history) == 2
    assert history[0]["status"] == "En attente"
    assert history[1]["status"] == "Révoqué"
    os.remove(store_path)
    print("OK - test_old_single_dict_format_migrated_transparently")


def test_attach_review_status_shows_latest_decision():
    """Le statut affiché sur chaque ligne doit être la DERNIÈRE décision,
    pas la première ou une décision intermédiaire."""
    store_path = tempfile.mktemp(suffix=".json")
    apply_review_decision("user1", "AD", "En attente", store_path=store_path)
    apply_review_decision("user1", "AD", "Révoqué", "reviewer02", "Final", store_path)

    df = pd.DataFrame({"username": ["user1"], "system": ["AD"]})
    df = attach_review_status(df, store_path=store_path)
    assert df.loc[0, "review_status"] == "Révoqué"
    assert df.loc[0, "validated_by"] == "reviewer02"
    os.remove(store_path)
    print("OK - test_attach_review_status_shows_latest_decision")


def test_no_history_defaults_to_en_attente():
    """Un compte sans aucune décision enregistrée doit rester 'En
    attente', comportement inchangé."""
    store_path = tempfile.mktemp(suffix=".json")
    history = get_audit_trail("never_reviewed", "AD", store_path)
    assert history == []
    df = pd.DataFrame({"username": ["never_reviewed"], "system": ["AD"]})
    df = attach_review_status(df, store_path=store_path)
    assert df.loc[0, "review_status"] == "En attente"
    print("OK - test_no_history_defaults_to_en_attente")


if __name__ == "__main__":
    test_multiple_decisions_are_appended_not_overwritten()
    test_old_single_dict_format_migrated_transparently()
    test_attach_review_status_shows_latest_decision()
    test_no_history_defaults_to_en_attente()
    print("Tous les tests passent.")
