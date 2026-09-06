"""Resolve analysis commands against BioFlow's own managed environments only.

BioFlow never falls back to a tool on PATH or to a system Conda installation.
Two machines with the same BioFlow setup must run the same binaries, and a
result must be traceable to the environment BioFlow installed. When a backend
is missing the stage refuses to start and names the component to install.
"""

from dataclasses import dataclass, field
from pathlib import Path

from backend.config import BioFlowConfig, get_config
from backend.setup.manager import DATABASE_PREFIX, ENVIRONMENT_PREFIX, SetupManager
from backend.setup.registry import database_specs, environment_specs


@dataclass
class MissingBackend(Exception):
    """Raised when a stage cannot run because its backend is not installed."""

    message: str
    #: Setup component keys, ready to hand to SetupManager.build_plan().
    components: list[str] = field(default_factory=list)

    def __str__(self) -> str:
        return self.message


class EnvironmentResolver:
    """Turn a tool invocation into a command inside BioFlow's own environment."""

    def __init__(self, config: BioFlowConfig | None = None):
        self.config = config or get_config()
        self.manager = SetupManager(self.config)

    # ------------------------------------------------------------------
    def check(self, environment_key: str, databases: tuple[str, ...] = ()) -> list[str]:
        """Return setup component keys that must be installed before running."""
        missing: list[str] = []
        if not self.manager.micromamba_installed():
            missing.append("micromamba")
        # The manager's check, not the configuration's: a bare `bin` directory
        # is left behind by an interrupted environment creation, and accepting
        # it here let the run button enable itself for an environment the Setup
        # page was simultaneously reporting as incomplete. The analysis then
        # failed part-way through instead of before it started.
        if not self.manager.environment_installed(environment_key):
            missing.append(f"{ENVIRONMENT_PREFIX}{environment_key}")
        specifications = database_specs()
        for database_key in databases:
            specification = specifications.get(database_key)
            if specification and not self.manager.database_installed(specification):
                missing.append(f"{DATABASE_PREFIX}{database_key}")
        return missing

    def describe(self, components: list[str]) -> str:
        """Human-readable names for missing components, for the error message."""
        environments, databases = environment_specs(), database_specs()
        names = []
        for component in components:
            if component == "micromamba":
                names.append("the Micromamba runtime")
            elif component.startswith(ENVIRONMENT_PREFIX):
                key = component[len(ENVIRONMENT_PREFIX):]
                specification = environments.get(key)
                names.append(f"the {specification.title.lower()} environment" if specification else key)
            elif component.startswith(DATABASE_PREFIX):
                key = component[len(DATABASE_PREFIX):]
                specification = databases.get(key)
                names.append(specification.title if specification else key)
        return ", ".join(names)

    def require(self, environment_key: str, databases: tuple[str, ...] = ()) -> None:
        """Raise MissingBackend unless every needed component is installed."""
        missing = self.check(environment_key, databases)
        if missing:
            raise MissingBackend(
                message=(
                    f"Cannot run: {self.describe(missing)} is not installed. "
                    f"Open Setup & Resources and install it."
                ),
                components=missing,
            )

    # ------------------------------------------------------------------
    def executable_path(self, environment_key: str, tool: str) -> Path:
        return self.config.environment_prefix(environment_key) / "bin" / tool

    def resolve(self, environment_key: str, command: list[str]) -> tuple[str, list[str]]:
        """Build the micromamba invocation that runs `command` in the environment.

        Raises MissingBackend rather than degrading to any other installation.
        """
        self.require(environment_key)
        tool = command[0]
        if not self.executable_path(environment_key, tool).exists():
            raise MissingBackend(
                message=(
                    f"'{tool}' is not present in BioFlow's "
                    f"{self.config.environment_name(environment_key)} environment. "
                    f"Reinstall it from Setup & Resources."
                ),
                components=[f"{ENVIRONMENT_PREFIX}{environment_key}"],
            )
        return str(self.config.micromamba_binary), [
            "run",
            "-r",
            str(self.config.micromamba_root),
            "-n",
            self.config.environment_name(environment_key),
            *command,
        ]
