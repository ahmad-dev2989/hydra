"""
HYDRA Direct Background App Execution Engine (HDBE)
Exposes the local vault manager, direct workers, and background task dispatcher.
"""

from .vault_manager import VAULT_MGR
from .direct_dispatcher import DIRECT_DISPATCHER
from .gmail_direct_worker import GMAIL_WORKER
from .classroom_direct_worker import CLASSROOM_WORKER
from .spotify_direct_worker import SPOTIFY_WORKER

__all__ = [
    "VAULT_MGR",
    "DIRECT_DISPATCHER",
    "GMAIL_WORKER",
    "CLASSROOM_WORKER",
    "SPOTIFY_WORKER"
]
