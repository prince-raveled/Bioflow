import os

from app.backend.runners.fastqc_runner import FastQCRunner
from app.backend.environments.qc_environment import QCEnvironment

class QCPipeline:

    def run(
        self,
        input_file: str,
        output_directory: str,
    ):

        if not QCEnvironment.exists():

            raise RuntimeError(
                "QC environment is not installed."
            )

        os.makedirs(
            output_directory,
            exist_ok=True,
        )

        runner = FastQCRunner()

        return runner.run(
            input_file,
            output_directory,
        )