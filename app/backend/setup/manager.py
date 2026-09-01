"""Report what BioFlow has installed and build the plan that installs the rest."""

from dataclasses import dataclass
from pathlib import Path
import os

from backend.config import (
    BOWTIE2_INDEX_EXTENSIONS,
    BOWTIE2_INDEX_PARTS,
    METAPHLAN_INDEX,
    BioFlowConfig,
    ResolvedReference,
    ResourceState,
    bowtie2_index_is_complete,
    bowtie2_index_is_partial,
    get_config,
)
from backend.setup import bootstrap
from backend.setup.plan import ActionStep, CommandStep, SetupPlan
from backend.setup.registry import (
    DATABASES,
    ENVIRONMENTS,
    MICROMAMBA_APPROXIMATE_BYTES,
    DatabaseSpec,
    EnvironmentSpec,
    database_specs,
)


RUNTIME_KEY = "micromamba"

#: Re-exported so callers have one import site for index validation.
__all__ = [
    "BOWTIE2_INDEX_PARTS",
    "BOWTIE2_INDEX_EXTENSIONS",
    "ComponentStatus",
    "SetupManager",
    "bowtie2_index_is_complete",
    "bowtie2_index_is_partial",
]
ENVIRONMENT_PREFIX = "env:"
DATABASE_PREFIX = "db:"


@dataclass(frozen=True)
class ComponentStatus:
    """One installable component as shown on the Setup page."""

    key: str
    kind: str  # "runtime", "environment", or "database"
    title: str
    description: str
    #: How the resource stands: missing, incomplete, managed, or external.
    state: ResourceState
    approximate_bytes: int
    #: The location actually in use, which for an external reference is not
    #: BioFlow's managed store.
    location: Path | None = None
    #: Where BioFlow would install its own copy, when that differs.
    managed_location: Path | None = None
    #: Environment key a database needs before it can be downloaded.
    requires_environment: str | None = None

    @property
    def installed(self) -> bool:
        """True when the resource can actually be used for an analysis."""
        return self.state.usable

    @property
    def is_managed(self) -> bool:
        return self.state is ResourceState.MANAGED

    def describe_state(self) -> str:
        """Status text that names both the state and the path in use."""
        if self.location is None or not self.state.usable:
            return self.state.label
        return f"{self.state.label} / {self.location}"


class SetupManager:
    """Single place that knows how each BioFlow backend component is provisioned."""

    def __init__(self, config: BioFlowConfig | None = None):
        self.config = config or get_config()

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------
    def micromamba_installed(self) -> bool:
        binary = self.config.micromamba_binary
        return binary.is_file() and os.access(binary, os.X_OK)

    def environment_installed(self, key: str) -> bool:
        return self.config.environment_is_installed(key)

    def database_directory(self, spec: DatabaseSpec) -> Path:
        return self.config.database_directory(*spec.directory_parts)

    def database_state(self, spec: DatabaseSpec) -> ResolvedReference:
        """How a database stands, and which copy would actually be used."""
        if spec.key == "grch38":
            return self.config.resolve_grch38_index()

        directory = self.database_directory(spec)
        managed = directory
        if directory.is_dir() and any(
            next(directory.glob(pattern), None) for pattern in spec.marker_globs
        ):
            return ResolvedReference(ResourceState.MANAGED, directory, managed)
        return ResolvedReference(ResourceState.MISSING, None, managed)

    def database_installed(self, spec: DatabaseSpec) -> bool:
        """True when the database can be used for an analysis right now.

        This is the run-time question, so an explicitly configured external
        reference satisfies it. Installation planning asks a different question:
        see managed_database_present().
        """
        return self.database_state(spec).usable

    def managed_database_present(self, spec: DatabaseSpec) -> bool:
        """True when BioFlow's own copy exists in its managed store.

        Installation is planned against this, never against resolution, so a
        user with an external reference can still install the managed copy.
        """
        if spec.key == "grch38":
            return bowtie2_index_is_complete(self.config.managed_grch38_index_prefix)
        directory = self.database_directory(spec)
        if not directory.is_dir():
            return False
        return any(next(directory.glob(pattern), None) for pattern in spec.marker_globs)

    def grch38_index_installed(self) -> bool:
        """True when a complete six-file index resolves from any allowed source."""
        return self.config.resolve_grch38_index().usable

    def components(self) -> list[ComponentStatus]:
        """Every component, in the order it must be installed."""
        statuses = [
            ComponentStatus(
                key=RUNTIME_KEY,
                kind="runtime",
                title="Micromamba runtime",
                description="BioFlow's private package manager. Every other component needs it.",
                state=(
                    ResourceState.MANAGED
                    if self.micromamba_installed()
                    else ResourceState.MISSING
                ),
                approximate_bytes=MICROMAMBA_APPROXIMATE_BYTES,
                location=self.config.micromamba_binary,
                managed_location=self.config.micromamba_binary,
            )
        ]
        for spec in ENVIRONMENTS:
            statuses.append(
                ComponentStatus(
                    key=f"{ENVIRONMENT_PREFIX}{spec.key}",
                    kind="environment",
                    title=f"{spec.title} tools",
                    description=spec.description,
                    state=(
                        ResourceState.MANAGED
                        if self.environment_installed(spec.key)
                        else ResourceState.MISSING
                    ),
                    approximate_bytes=spec.approximate_bytes,
                    location=self.config.environment_prefix(spec.key),
                    managed_location=self.config.environment_prefix(spec.key),
                )
            )
        for spec in DATABASES:
            resolved = self.database_state(spec)
            statuses.append(
                ComponentStatus(
                    key=f"{DATABASE_PREFIX}{spec.key}",
                    kind="database",
                    title=spec.title,
                    description=spec.description,
                    state=resolved.state,
                    approximate_bytes=spec.approximate_bytes,
                    # The path actually in use, so an external reference is never
                    # reported against BioFlow's own empty managed directory.
                    location=resolved.prefix or resolved.managed_prefix,
                    managed_location=resolved.managed_prefix,
                    requires_environment=spec.environment_key,
                )
            )
        return statuses

    def estimated_bytes(self, keys: list[str]) -> int:
        """Approximate footprint of a selection, including anything it pulls in."""
        selected = set(self._expand(keys))
        databases = database_specs()
        total = 0
        for component in self.components():
            if component.key not in selected:
                continue
            if component.kind == "database":
                spec = databases[component.key[len(DATABASE_PREFIX):]]
                if self.managed_database_present(spec):
                    continue
            elif component.installed:
                continue
            total += component.approximate_bytes
        return total

    # ------------------------------------------------------------------
    # Planning
    # ------------------------------------------------------------------
    def build_plan(self, keys: list[str]) -> SetupPlan:
        """Turn a component selection into an ordered, dependency-complete plan."""
        selected = set(self._expand(keys))
        plan = SetupPlan()
        if not selected:
            return plan

        if not self.micromamba_installed():
            plan.components.append("Micromamba runtime")
            plan.estimated_bytes += MICROMAMBA_APPROXIMATE_BYTES
            plan.add(
                ActionStep(
                    "Install the Micromamba runtime",
                    lambda log: bootstrap.install_micromamba(self.config, log),
                )
            )

        for spec in ENVIRONMENTS:
            if f"{ENVIRONMENT_PREFIX}{spec.key}" not in selected:
                continue
            if self.environment_installed(spec.key):
                continue
            plan.components.append(spec.title)
            plan.estimated_bytes += spec.approximate_bytes
            plan.add(*self._environment_steps(spec))

        for spec in DATABASES:
            if f"{DATABASE_PREFIX}{spec.key}" not in selected:
                continue
            if self.managed_database_present(spec):
                continue
            plan.components.append(spec.title)
            plan.estimated_bytes += spec.approximate_bytes
            plan.add(*self._database_steps(spec))

        return plan

    def _expand(self, keys: list[str]) -> list[str]:
        """Add the environments that the selected databases need."""
        expanded = list(dict.fromkeys(keys))
        for spec in DATABASES:
            if f"{DATABASE_PREFIX}{spec.key}" not in expanded:
                continue
            environment_key = f"{ENVIRONMENT_PREFIX}{spec.environment_key}"
            if environment_key not in expanded and not self.environment_installed(spec.environment_key):
                expanded.append(environment_key)
        return expanded

    # ------------------------------------------------------------------
    # Step construction
    # ------------------------------------------------------------------
    def _in_environment(self, title: str, environment_key: str, command: tuple[str, ...], **kwargs) -> CommandStep:
        """Wrap a command so it runs inside one of BioFlow's own environments."""
        return CommandStep(
            title=title,
            program=str(self.config.micromamba_binary),
            arguments=(
                "run",
                "-r",
                str(self.config.micromamba_root),
                "-n",
                self.config.environment_name(environment_key),
                *command,
            ),
            **kwargs,
        )

    def _environment_steps(self, spec: EnvironmentSpec) -> list:
        name = self.config.environment_name(spec.key)
        channels: list[str] = []
        for channel in spec.channels:
            channels.extend(["-c", channel])
        steps = [
            CommandStep(
                title=f"Create the {spec.title.lower()} environment ({name})",
                program=str(self.config.micromamba_binary),
                arguments=(
                    "create",
                    "-y",
                    "-r",
                    str(self.config.micromamba_root),
                    "-n",
                    name,
                    *channels,
                    *spec.packages,
                ),
                # Package mirrors time out often enough to be worth retrying, and
                # the package cache makes a retry cheap.
                retries=2,
            )
        ]
        for command in spec.verify_commands:
            steps.append(
                self._in_environment(f"Verify {command[0]}", spec.key, command)
            )
        return steps

    def _threads(self) -> str:
        return str(max(1, min(8, os.cpu_count() or self.config.default_threads)))

    def _database_steps(self, spec: DatabaseSpec) -> list:
        builders = {
            "grch38": self._grch38_steps,
            "metaphlan_chocophlan": self._metaphlan_database_steps,
            "humann_chocophlan": self._humann_chocophlan_steps,
            "humann_uniref50": self._humann_uniref_steps,
        }
        builder = builders.get(spec.key)
        if builder is None:
            raise KeyError(f"No installation steps are defined for database '{spec.key}'.")
        return builder(spec)

    @staticmethod
    def _make_directory_step(directory: Path) -> ActionStep:
        def action(log):
            directory.mkdir(parents=True, exist_ok=True)
            log(f"Prepared {directory}")

        return ActionStep(f"Prepare {directory}", action)

    def _grch38_steps(self, spec: DatabaseSpec) -> list:
        # Installation always writes into BioFlow's managed store, whatever an
        # external or development reference may be configured for run time.
        directory = self.config.managed_grch38_directory
        reference = directory / "GRCh38.primary_assembly.genome.fa.gz"
        return [
            self._make_directory_step(directory),
            ActionStep(
                "Download the GRCh38 human reference",
                lambda log: bootstrap.download_grch38(self.config, log),
            ),
            self._in_environment(
                "Build the GRCh38 Bowtie2 index (this takes a while)",
                spec.environment_key,
                (
                    "bowtie2-build",
                    "--threads",
                    self._threads(),
                    str(reference),
                    str(self.config.managed_grch38_index_prefix),
                ),
            ),
        ]

    def _metaphlan_database_steps(self, spec: DatabaseSpec) -> list:
        directory = self.database_directory(spec)
        return [
            self._make_directory_step(directory),
            self._in_environment(
                f"Download the MetaPhlAn marker database ({METAPHLAN_INDEX})",
                spec.environment_key,
                (
                    "metaphlan",
                    "--install",
                    "--index",
                    METAPHLAN_INDEX,
                    "--bowtie2db",
                    str(directory),
                    "--nproc",
                    self._threads(),
                ),
                retries=2,
            ),
        ]

    def _humann_chocophlan_steps(self, spec: DatabaseSpec) -> list:
        parent = self.config.humann_database_directory
        return [
            self._make_directory_step(parent),
            self._in_environment(
                "Download the HUMAnN ChocoPhlAn database",
                spec.environment_key,
                ("humann_databases", "--download", "chocophlan", "full", str(parent), "--update-config", "yes"),
                retries=2,
            ),
            self._in_environment(
                "Point HUMAnN at the nucleotide database",
                spec.environment_key,
                (
                    "humann_config",
                    "--update",
                    "database_folders",
                    "nucleotide",
                    str(self.database_directory(spec)),
                ),
            ),
        ]

    def _humann_uniref_steps(self, spec: DatabaseSpec) -> list:
        parent = self.config.humann_database_directory
        return [
            self._make_directory_step(parent),
            self._in_environment(
                "Download the HUMAnN UniRef50 DIAMOND database",
                spec.environment_key,
                (
                    "humann_databases",
                    "--download",
                    "uniref",
                    "uniref50_diamond",
                    str(parent),
                    "--update-config",
                    "yes",
                ),
                retries=2,
            ),
            self._in_environment(
                "Point HUMAnN at the protein database",
                spec.environment_key,
                (
                    "humann_config",
                    "--update",
                    "database_folders",
                    "protein",
                    str(self.database_directory(spec)),
                ),
            ),
        ]
