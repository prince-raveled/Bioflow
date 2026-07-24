from pathlib import Path
from datetime import datetime
import json


class Project:
    """
    Represents a BioFlow analysis project.
    """

    def __init__(
        self,
        name: str,
        location: str,
        paired_end: bool = True,
        threads: int = 4,
    ):

        self.name = name
        self.location = Path(location)

        self.paired_end = paired_end
        self.threads = threads

        self.created = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        self.status = "created"

        self.pipeline = {
            "fastqc": True,
            "fastp": True,
            "host_removal": True,
            "metaphlan": True,
            "humann": True,
            "multiqc": True,
        }

    def create_structure(self):
        """
        Create the directory structure for a BioFlow project.
        """

        folders = [
            "raw",
            "trimmed",
            "host_removed",
            "fastqc",
            "metaphlan",
            "humann",
            "multiqc",
            "results",
            "reports",
            "logs",
        ]

        self.location.mkdir(parents=True, exist_ok=True)

        for folder in folders:
            (self.location / folder).mkdir(exist_ok=True)

    def save(self):
        """
        Save project metadata to project.json.
        """

        data = {
            "name": self.name,
            "paired_end": self.paired_end,
            "threads": self.threads,
            "created": self.created,
            "status": self.status,
            "version": "1.0.0",
            "pipeline": self.pipeline,
        }

        with open(self.location / "project.json", "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4)