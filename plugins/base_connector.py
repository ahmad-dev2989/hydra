"""
HYDRA Direct Background Worker Base Connector (Phase 1)
Defines the uniform interface for all background app execution workers.
"""

from abc import ABC, abstractmethod
from typing import Dict, Any, Tuple


class BaseDirectWorker(ABC):
    """Abstract Base Class for zero-UI, background execution plugins."""

    app_id: str = ""
    app_name: str = ""

    @abstractmethod
    def is_ready(self) -> bool:
        """Returns True if the worker has valid credentials and is ready for background execution."""
        pass

    @abstractmethod
    def execute_task(self, task_type: str, raw_query: str, **kwargs) -> Tuple[bool, str, Dict[str, Any]]:
        """
        Executes a task headlessly in the background.
        Returns:
            (success: bool, speech_feedback: str, details_dict: dict)
        """
        pass
