"""
FastQC Wrapper for BioFlow.
"""

from pathlib import Path

from tools.base_tool import BaseTool
from backend.runner import Runner


class FastQC(BaseTool):
    """
    Wrapper class for running FastQC.
    """

    def __init__(self, project):

        super().__init__(project)

        self.name = "FastQC"

        self.input_dir = self.project.location / "raw"

        self.output_dir = self.project.location / "fastqc"

        # HPC Conda environment
        self.env_name = "fastqc"

    def run(self):
        """
        Execute FastQC on all FASTQ files in the raw directory.
        """

        self.status = "running"

        print(f"\n========== {self.name} ==========")

        # Create output directory if missing
        self.output_dir.mkdir(parents=True, exist_ok=True)

        # Find FASTQ files
        fastq_files = list(self.input_dir.glob("*.fastq")) + \
                      list(self.input_dir.glob("*.fastq.gz"))

        if not fastq_files:
            print("No FASTQ files found.")
            self.fail()
            return False

        for file in fastq_files:

            print(f"Processing: {file.name}")

            command = [
                "conda",
                "run",
                "-n",
                self.env_name,
                "fastqc",
                str(file),
                "-o",
                str(self.output_dir)
            ]

            success, stdout, stderr = Runner.execute(command)

            if success:
                print(f"✓ Completed: {file.name}")
            else:
                print(f"✗ Failed: {file.name}")
                print(stderr)
                self.fail()
                return False

        self.finish()

        print("FastQC finished successfully.\n")

        return True