import os

from app.backend.runners.humann_runner import HumannRunner
from app.backend.environments.functional_environment import FunctionalEnvironment

class FunctionalPipeline:

    def run(
        self,
        input_file: str,
        output_directory: str,
    ):

        if not FunctionalEnvironment.exists():

            raise RuntimeError(
                "Functional environment is not installed."
            )

        os.makedirs(
            output_directory,
            exist_ok=True,
        )

        runner = HumannRunner()

        return runner.run(
            input_file,
            output_directory,
        )
