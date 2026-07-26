import os

from app.backend.environments.mag_environment import MAGEnvironment


class MAGPipeline:
    """
    Pipeline for Metagenome-Assembled Genome (MAG) analysis.
    """

    def run(
        self,
        input_file: str,
        output_directory: str,
    ):

        if not MAGEnvironment.exists():

            raise RuntimeError(
                "MAG environment is not installed."
            )

        os.makedirs(
            output_directory,
            exist_ok=True,
        )

        print(f"Running MAG pipeline on {input_file}")
        print(f"Output directory: {output_directory}")

        return True
