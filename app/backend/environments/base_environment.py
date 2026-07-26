import subprocess


class BaseEnvironment:
    """
    Base class for Conda environments.
    """

    NAME = "bioflow"

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
        Create the Conda environment.
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
