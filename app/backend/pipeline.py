"""
Pipeline Manager for BioFlow

Responsible for:
- Managing pipeline execution
- Tracking progress
- Tracking status
- Executing enabled pipeline steps
"""

from backend.project import Project


class Pipeline:
    """
    Controls the execution of a BioFlow project.
    """

    def __init__(self, project: Project):

        self.project = project

        # Pipeline state
        self.status = "idle"
        self.progress = 0

        # Pipeline steps
        self.steps = project.pipeline

        # Execution order
        self.execution_order = [
            "fastqc",
            "fastp",
            "host_removal",
            "metaphlan",
            "humann",
            "multiqc"
        ]

    def run(self):
        """
        Execute the BioFlow pipeline.
        """

        self.status = "running"

        print("=" * 50)
        print(f"Starting BioFlow Project : {self.project.name}")
        print("=" * 50)

        total_steps = len(self.execution_order)
        completed_steps = 0

        for step in self.execution_order:

            if self.steps.get(step):

                print(f"Running: {step}")

                # Placeholder
                # Tool wrappers will be called here later

                completed_steps += 1
                self.progress = int(
                    (completed_steps / total_steps) * 100
                )

                print(f"Completed: {step}")
                print(f"Progress : {self.progress}%")
                print()

        self.status = "finished"

        print("=" * 50)
        print("Pipeline Finished Successfully")
        print("=" * 50)

    def show_status(self):
        """
        Display pipeline status.
        """

        print(f"Status   : {self.status}")
        print(f"Progress : {self.progress}%")