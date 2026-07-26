from app.backend.runners.conda_runner import CondaRunner


class MetaphlanRunner:

    ENVIRONMENT = "bioflow-taxonomy"

    def run(
        self,
        input_file: str,
        output_file: str,
    ):

        command = [
            "metaphlan",
            input_file,
            "-o", output_file,
        ]

        return CondaRunner.run(
            self.ENVIRONMENT,
            command,
        )
