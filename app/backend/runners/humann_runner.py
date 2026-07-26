from app.backend.runners.conda_runner import CondaRunner


class HumannRunner:

    ENVIRONMENT = "bioflow-functional"

    def run(
        self,
        input_file: str,
        output_directory: str,
    ):

        command = [
            "humann",
            "-i", input_file,
            "-o", output_directory,
        ]

        return CondaRunner.run(
            self.ENVIRONMENT,
            command,
        )
