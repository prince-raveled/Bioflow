import subprocess


class FunctionalEnvironment:
    """
    Conda environment for functional analysis.
    """

    NAME = "bioflow-functional"

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
        Create the Conda environment with functional analysis tools.
        """

        subprocess.run(
            [
                "conda",
                "create",
                "-n",
                cls.NAME,
                "-y",
                "humann",
            ],
            check=True,
        )
