"""
Base class for all BioFlow analysis tools.
"""

from abc import ABC, abstractmethod
from pathlib import Path


class BaseTool(ABC):
    """
    Abstract base class for BioFlow tools.
    """

    def __init__(self, project):

        self.project = project

        self.status = "idle"

        self.input_dir = None

        self.output_dir = None

    def validate(self):
        """
        Validate input before execution.
        """

        return True

    @abstractmethod
    def run(self):
        """
        Execute the tool.
        """
        pass

    def finish(self):

        self.status = "finished"

    def fail(self):

        self.status = "failed"