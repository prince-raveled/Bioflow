import subprocess


class QCEnvironment:

    NAME = "bioflow-qc"

    @classmethod
    def exists(cls):

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