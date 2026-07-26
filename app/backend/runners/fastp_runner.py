from app.backend.runners.conda_runner import CondaRunner


class FastPRunner:

    ENVIRONMENT = "bioflow-qc"

    def run(self, input_file, output_file, html_file, json_file):

        command = [
            "fastp",
            "-i", input_file,
            "-o", output_file,
            "-h", html_file,
            "-j", json_file,
        ]

        return CondaRunner.run(
            self.ENVIRONMENT,
            command,
        )