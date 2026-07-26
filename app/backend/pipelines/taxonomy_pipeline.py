import os

from app.backend.runners.metaphlan_runner import MetaphlanRunner
from app.backend.environments.taxonomy_environment import TaxonomyEnvironment

class TaxonomyPipeline:

    def run(
        self,
        input_file: str,
        output_directory: str,
    ):

        if not TaxonomyEnvironment.exists():

            raise RuntimeError(
                "Taxonomy environment is not installed."
            )

        os.makedirs(
            output_directory,
            exist_ok=True,
        )

        runner = MetaphlanRunner()

        return runner.run(
            input_file,
            output_directory,
        )
