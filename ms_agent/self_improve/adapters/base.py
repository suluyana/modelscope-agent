from typing import Dict, Any, Tuple
from abc import ABC, abstractmethod

class RunAdapter(ABC):
    @property
    @abstractmethod
    def name(self) -> str:
        pass

    @property
    @abstractmethod
    def output_dir(self) -> str:
        pass

    @abstractmethod
    def run_target(self) -> Tuple[bool, Dict[str, Any]]:
        """
        Runs the target task/benchmark.
        Returns:
            Tuple[bool, Dict[str, Any]]: 
            - bool: success/fail of the run (framework level)
            - Dict: context containing exit_code, reward, etc.
        """
        pass

    @abstractmethod
    def get_context(self) -> Dict[str, str]:
        """
        Returns adapter-specific template variables for verification.
        """
        pass
