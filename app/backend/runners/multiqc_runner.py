from app.backend.runners.conda_runner import CondaRunner


class MultiQCRunner:

    ENVIRONMENT = "bioflow-qc"

    def run(self, input_directory, output_directory):

        command = [
            "multiqc",
            input_directory,
            "-o",
            output_directory,
        ]

        return CondaRunner.run(
            self.ENVIRONMENT,
            command,
        )