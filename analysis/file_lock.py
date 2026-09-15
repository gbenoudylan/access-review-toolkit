"""
Verrou de fichier portable, sans dépendance externe — pour protéger les
cycles lecture-modification-écriture des magasins JSON partagés
(décisions de revue, historique de tendance) contre les écritures
concurrentes.

Trouvé en poussant la fiabilité au maximum : sans verrou, deux
utilisateurs enregistrant une décision au même moment (scénario réaliste
pour une équipe de plusieurs reviewers) peuvent silencieusement s'écraser
l'un l'autre — chacun lit le fichier avant l'écriture de l'autre, donc
chacun écrit une version qui ignore la mise à jour de l'autre ("perte de
mise à jour" classique), voire produire un fichier JSON corrompu si les
deux écritures se chevauchent physiquement.

Implémenté avec os.open(..., O_CREAT | O_EXCL) plutôt qu'une bibliothèque
tierce (ex. filelock) : cette primitive est atomique aussi bien sous
POSIX que sous Windows, suffisante pour ce cas d'usage simple, et évite
d'ajouter une dépendance à installer pour un besoin aussi ciblé.
"""

from __future__ import annotations
import os
import time
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def locked(path: Path | str, timeout: float = 5.0, retry_interval: float = 0.05):
    """
    Verrou exclusif basé sur un fichier '<path>.lock' — à utiliser autour
    de tout cycle lecture-modification-écriture sur un fichier partagé
    entre plusieurs processus/threads (ex. plusieurs onglets du
    dashboard, ou plusieurs utilisateurs).

    Un verrou resté en place plus longtemps que `timeout` est considéré
    abandonné (ex. process précédent interrompu sans nettoyer) et retiré
    de force plutôt que de bloquer indéfiniment un usage légitime —
    mieux vaut un risque résiduel rare de collision qu'un blocage permanent.
    """
    lock_path = Path(f"{path}.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    start = time.time()
    acquired = False
    while not acquired:
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(fd)
            acquired = True
        except FileExistsError:
            if time.time() - start > timeout:
                try:
                    os.remove(str(lock_path))
                except OSError:
                    pass
                continue
            time.sleep(retry_interval)
    try:
        yield
    finally:
        try:
            os.remove(str(lock_path))
        except OSError:
            pass
