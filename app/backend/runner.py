"""
Command Runner for BioFlow.
"""

import subprocess


class Runner:

    @staticmethod
    def execute(command):
        """
        Execute an external command.

        Returns
        -------
        (success, stdout, stderr)
        """

        try:

            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                check=True
            )

            return True, result.stdout, result.stderr

        except subprocess.CalledProcessError as e:

            return False, e.stdout, e.stderrwe 