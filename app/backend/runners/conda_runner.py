import subprocess


class CondaRunner:
    """
    Executes commands inside a Conda environment.
    """

    @staticmethod
    def run(environment: str, command: list):

        cmd = [
            "conda",
            "run",
            "-n",
            environment,
        ] + command

        try:

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=True,
            )

            return result

        except subprocess.CalledProcessError as e:

            raise RuntimeError(
                f"\nCommand failed:\n{' '.join(cmd)}\n\n"
                f"STDOUT:\n{e.stdout}\n\n"
                f"STDERR:\n{e.stderr}"
            )