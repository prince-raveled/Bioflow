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
from backend.release import component_is_available
from backend.resources import usable_cpus
from backend.setup import bootstrap
from backend.setup.plan import ActionStep, CommandStep, SetupPlan
from backend.setup.registry import (
    DATABASES,
    ENVIRONMENTS,
    MICROMAMBA_APPROXIMATE_BYTES,
    DatabaseSpec,
    EnvironmentSpec,
    database_specs,
    environment_specs,
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
    #: For a partly installed dataset, which required files are still absent.
    missing_files: tuple[str, ...] = ()

    @property
    def installed(self) -> bool:
        """True when the resource can actually be used for an analysis."""
        return self.state.usable

    @property
    def is_managed(self) -> bool:
        return self.state is ResourceState.MANAGED

    def describe_state(self) -> str:
        """Status text that names both the state and the path in use."""
        if self.state is ResourceState.INCOMPLETE and self.missing_files:
            # Name what is absent: "incomplete" alone leaves a user with tens
            # of gigabytes on disk no way to tell what went wrong.
            listed = ", ".join(self.missing_files[:3])
            if len(self.missing_files) > 3:
                listed += f", and {len(self.missing_files) - 3} more"
            return f"{self.state.label} - still missing {listed}"
        if self.location is None or not self.state.usable:
            return self.state.label
        return f"{self.state.label} / {self.location}"


#: A completed dataset must reach at least this share of its expected size.
#: Set low deliberately: the registry sizes are estimates, and a false failure
#: here would block an install that actually succeeded.
MINIMUM_INSTALLED_FRACTION = 0.5


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
        """True when the environment exists and its tools are actually there.

        A `bin` directory alone is not enough: an interrupted environment
        creation leaves one behind, and reporting that as installed defers the
        failure to the middle of an analysis run instead of showing it here.
        """
        if not self.config.environment_is_installed(key):
            return False
        return not self.missing_environment_tools(key)

    def _environment_state(self, key: str) -> ResourceState:
        """Managed, missing, or created but short of the tools it should hold."""
        if not self.config.environment_is_installed(key):
            return ResourceState.MISSING
        if self.missing_environment_tools(key):
            return ResourceState.INCOMPLETE
        return ResourceState.MANAGED

    def missing_environment_tools(self, key: str) -> list[str]:
        """Which of an environment's declared tools are absent from it."""
        spec = environment_specs().get(key)
        if spec is None:
            return []
        binaries = self.config.environment_prefix(key) / "bin"
        expected = {command[0] for command in spec.verify_commands}
        return sorted(name for name in expected if not (binaries / name).exists())

    def database_directory(self, spec: DatabaseSpec) -> Path:
        return self.config.database_directory(*spec.directory_parts)

    def database_state(self, spec: DatabaseSpec) -> ResolvedReference:
        """How a database stands, and which copy would actually be used."""
        if spec.key == "grch38":
            return self.config.resolve_grch38_index()

        directory = self.database_directory(spec)
        managed = directory
        if self.managed_database_present(spec):
            return ResolvedReference(ResourceState.MANAGED, directory, managed)
        # Distinguish a half-finished install from one never started. This data
        # runs to tens of gigabytes, so a partially unpacked dataset must not be
        # reported as absent: that invites a full re-download. Both pattern sets
        # count, so a leftover from an earlier release of the dataset is noticed
        # too rather than leaving the directory looking empty.
        patterns = (*spec.required_globs, *spec.marker_globs)
        if directory.is_dir() and any(
            next(directory.glob(pattern), None) for pattern in patterns
        ):
            return ResolvedReference(ResourceState.INCOMPLETE, None, managed)
        return ResolvedReference(ResourceState.MISSING, None, managed)

    def database_installed(self, spec: DatabaseSpec) -> bool:
        """True when the database can be used for an analysis right now.

        This is the run-time question, so an explicitly configured external
        reference satisfies it. Installation planning asks a different question:
        see managed_database_present().
        """
        return self.database_state(spec).usable

    def missing_database_files(self, spec: DatabaseSpec) -> list[str]:
        """Which required files of a dataset are absent, if any."""
        directory = self.database_directory(spec)
        if not spec.required_globs:
            return []
        return [
            pattern
            for pattern in spec.required_globs
            if next(directory.glob(pattern), None) is None
        ]

    def managed_database_present(self, spec: DatabaseSpec) -> bool:
        """True when BioFlow's own copy exists in its managed store, complete.

        Installation is planned against this, never against resolution, so a
        user with an external reference can still install the managed copy.
        """
        if spec.key == "grch38":
            return bowtie2_index_is_complete(self.config.managed_grch38_index_prefix)
        directory = self.database_directory(spec)
        if not directory.is_dir():
            return False
        if spec.required_globs:
            # Every required file, not merely a marker: a part-unpacked dataset
            # must not present itself as ready to use.
            return not self.missing_database_files(spec)
        return any(next(directory.glob(pattern), None) for pattern in spec.marker_globs)

    def grch38_index_installed(self) -> bool:
        """True when a complete six-file index resolves from any allowed source."""
        return self.config.resolve_grch38_index().usable

    def components(self) -> list[ComponentStatus]:
        """Every component this release offers, in installation order.

        Components withheld from the release are filtered out here rather than
        removed from the registry, so nothing that depends on their definitions
        breaks and re-enabling them needs no change to this file.
        """
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
                    state=self._environment_state(spec.key),
                    approximate_bytes=spec.approximate_bytes,
                    missing_files=tuple(self.missing_environment_tools(spec.key)),
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
                    missing_files=(
                        tuple(self.missing_database_files(spec))
                        if resolved.state is ResourceState.INCOMPLETE
                        else ()
                    ),
                )
            )
        return [s for s in statuses if component_is_available(s.key)]

    def _selection_total(self, keys: list[str], database_size) -> int:
        """Sum a selection, sizing databases with the supplied measure."""
        selected = set(self._expand(keys))
        databases = database_specs()
        total = 0
        for component in self.components():
            if component.key not in selected:
                continue
            if component.kind == "database":
                spec = databases[component.key[len(DATABASE_PREFIX):]]
                if not self.managed_database_present(spec):
                    total += database_size(spec)
            elif not component.installed:
                total += component.approximate_bytes
        return total

    def required_memory_bytes(self, keys: list[str]) -> int:
        """Memory the heaviest selected component needs to run.

        Components run one at a time, so the requirement is the maximum rather
        than the sum. Unlike the disk estimates this counts what is already
        installed: a database that is present still needs its memory when used.
        """
        specs = database_specs()
        return max(
            (
                specs[key.removeprefix("db:")].runtime_memory_bytes
                for key in keys
                if key.startswith("db:") and key.removeprefix("db:") in specs
            ),
            default=0,
        )

    def estimated_peak_bytes(self, keys: list[str]) -> int:
        """Peak disk a selection needs, counting archives beside their contents.

        Gating on the installed footprint alone under-reserves: a dataset that
        unpacks in place needs room for both at once.
        """
        return self._selection_total(keys, lambda spec: spec.peak_disk())

    def estimated_download_bytes(self, keys: list[str]) -> int:
        """Bytes a selection actually pulls over the network."""
        return self._selection_total(keys, lambda spec: spec.transfer_size())

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
    def available_keys(self, keys: list[str]) -> list[str]:
        """The requested components this release actually offers.

        Applied after dependency expansion, so asking for a withheld database
        cannot pull in the environment that installs it either.
        """
        return [key for key in self._expand(keys) if component_is_available(key)]

    def withheld_keys(self, keys: list[str]) -> list[str]:
        """Requested components this release does not offer, for reporting."""
        return [key for key in dict.fromkeys(keys) if not component_is_available(key)]

    def build_plan(self, keys: list[str]) -> SetupPlan:
        """Turn a component selection into an ordered, dependency-complete plan.

        Components the release withholds are dropped here rather than only
        being hidden from the listing. The interface never offers them, but the
        headless CLI takes whatever key it is given, and installing a tool the
        application will not run wastes a large download on something
        unreachable.
        """
        selected = set(self.available_keys(keys))
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

        installed_environments = False
        for spec in ENVIRONMENTS:
            if f"{ENVIRONMENT_PREFIX}{spec.key}" in selected and not self.environment_installed(spec.key):
                installed_environments = True
                break

        for spec in DATABASES:
            if f"{DATABASE_PREFIX}{spec.key}" not in selected:
                continue
            if self.managed_database_present(spec):
                continue
            plan.components.append(spec.title)
            plan.estimated_bytes += spec.approximate_bytes
            plan.add(*self._database_steps(spec))

        # Last, so that a retry earlier in the plan still finds the cache warm:
        # creating an environment downloads packages that a later step may need
        # again, and the cache is what makes those retries cheap.
        if installed_environments:
            plan.add(self._cache_cleanup_step())

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

    def _cache_cleanup_step(self) -> CommandStep:
        """Drop package archives kept only to make an install retryable.

        Micromamba hardlinks packages from its cache into each environment, so
        most of what the cache appears to hold costs nothing to keep. Only the
        entries no environment references are removed, which on a measured
        install was 2.8 GB of the 7.4 GB the cache reported.

        `clean` takes no -r, so the root prefix is passed the only way it reads.
        """
        return CommandStep(
            title="Reclaim disk space from the package cache",
            program=str(self.config.micromamba_binary),
            arguments=("clean", "--tarballs", "--packages", "--index-cache", "--yes"),
            environment={"MAMBA_ROOT_PREFIX": str(self.config.micromamba_root)},
            # Reclaiming space is housekeeping; failing it must not fail an
            # install that has otherwise completed.
            tolerate_failure=True,
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
        return str(max(1, min(8, usable_cpus() or self.config.default_threads)))

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
        # Every dataset ends the same way: reclaim the download, then prove the
        # result is actually usable rather than trusting the tool's exit code.
        return [*builder(spec), self._reclaim_step(spec), self._verify_step(spec)]

    def _reclaim_step(self, spec: DatabaseSpec) -> ActionStep:
        directory = self.database_directory(spec)

        def action(log):
            bootstrap.reclaim_archives(directory, log, keep=spec.required_globs)

        return ActionStep(f"Reclaim disk space from the {spec.title} download", action)

    def _verify_step(self, spec: DatabaseSpec) -> ActionStep:
        def action(log):
            if spec.key == "grch38":
                prefix = self.config.managed_grch38_index_prefix
                if not bowtie2_index_is_complete(prefix):
                    raise RuntimeError(
                        f"The GRCh38 Bowtie2 index at {prefix} is incomplete. "
                        f"Run the install again to finish it."
                    )
                log(f"Verified the GRCh38 Bowtie2 index at {prefix}.")
                return
            missing = self.missing_database_files(spec)
            if missing:
                raise RuntimeError(
                    f"{spec.title} finished but these files are missing from "
                    f"{self.database_directory(spec)}: {', '.join(missing)}. "
                    f"Run the install again; completed downloads are reused."
                )
            directory = self.database_directory(spec)
            total = sum(p.stat().st_size for p in directory.rglob("*") if p.is_file())
            # A dataset of thousands of files can satisfy a glob while being
            # mostly absent, so weigh the result against its expected size.
            # The margin is wide because the registry figures are estimates.
            floor = int(spec.approximate_bytes * MINIMUM_INSTALLED_FRACTION)
            if total < floor:
                raise RuntimeError(
                    f"{spec.title} holds only {total / (1024 ** 3):.1f} GB in "
                    f"{directory}, well short of the {spec.approximate_bytes / (1024 ** 3):.0f} GB "
                    f"expected. The download looks truncated; run the install again."
                )
            log(f"Verified {spec.title}: {total / (1024 ** 3):.1f} GB in {directory}.")

        return ActionStep(f"Check that {spec.title} downloaded completely", action)

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
            ActionStep(
                "Remove the GRCh38 FASTA now that the index is built",
                lambda log: self._discard_reference(reference, log),
            ),
        ]

    def _discard_reference(self, reference: Path, log) -> None:
        """Delete the source FASTA once the index built from it is complete."""
        if not bowtie2_index_is_complete(self.config.managed_grch38_index_prefix):
            log("Keeping the FASTA: the index is not complete yet.")
            return
        if not reference.is_file():
            log("The GRCh38 FASTA was already removed.")
            return
        size = reference.stat().st_size
        reference.unlink()
        log(f"Removed {reference.name}, reclaiming {size / (1024 ** 3):.1f} GB.")

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
                    # --db_dir replaced --bowtie2db in MetaPhlAn 4.1; the old
                    # spelling is rejected outright by 4.2. The profiling stage
                    # already used the current name.
                    "--db_dir",
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
