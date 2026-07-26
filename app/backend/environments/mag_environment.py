import subprocess


class MAGEnvironment:
    """
    Conda environment for metagenome-assembled genome (MAG) analysis.
    """

    NAME = "bioflow-mag"

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
        Create the Conda environment for MAG analysis.
        """

        subprocess.run(
            [
                "conda",
                "create",
                "-n",
                cls.NAME,
                "-y",
            ],
            check=True,
        )
