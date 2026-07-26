from app.backend.runners.conda_runner import CondaRunner


class FastQCRunner:

    ENVIRONMENT = "bioflow-qc"

    def run(
        self,
        input_file: str,
        output_directory: str,
    ):

        command = [
            "fastqc",
            input_file,
            "-o",
            output_directory,
        ]

        return CondaRunner.run(
            self.ENVIRONMENT,
            command,
        )