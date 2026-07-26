import subprocess


class TaxonomyEnvironment:
    """
    Conda environment for taxonomic profiling.
    """

    NAME = "bioflow-taxonomy"

    @classmethod
    def exists(cls):
        """
        Check if the Conda environment exists.
        """

        result = subprocess.run(
            [
                "conda",
                "env",
                "list",
            ],
            capture_output=True,
            text=True,
        )

        return cls.NAME in result.stdout

    @classmethod
    def create(cls):
        """
        Create the Conda environment with taxonomic profiling tools.
        """

        subprocess.run(
            [
                "conda",
                "create",
                "-n",
                cls.NAME,
                "-y",
                "metaphlan",
            ],
            check=True,
        )
