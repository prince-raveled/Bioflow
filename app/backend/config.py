"""Central configuration for every BioFlow path, environment, and reference.

No other module hard-codes a home directory, environment name, or database
location: they all resolve through the configuration loaded here, so a user can
relocate BioFlow's backend data without editing code.

Reference resolution is deliberately explicit. BioFlow distinguishes between the
*managed* copy of a resource, which it installs and owns, and an *external* copy
that the user has deliberately configured. Installation always targets the
managed location; only run-time resolution may prefer an external one.
"""

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
import json
import os


DEFAULT_ENVIRONMENT_NAMES: dict[str, str] = {
    "qc": "bioflow-qc",
    "hostrem": "bioflow-hostrem",
    "taxonomy": "bioflow-taxonomy",
    "function": "bioflow-humann",
}

#: MetaPhlAn marker-gene database released January 2025.
METAPHLAN_INDEX = "mpa_vJan25_CHOCOPhlAnSGB_202503"

#: Backends analysis commands can run through. Native first because it is the
#: default and the one that needs nothing installed beyond BioFlow itself.
EXECUTION_BACKENDS = ("native", "container")

#: The analysis image used when the container backend is selected.
DEFAULT_CONTAINER_IMAGE = "localhost/bioflow-tools:0.1.0"

#: Environment variable reserved for development and automated tests only.
#: It never changes an installation target and never makes a resource report as
#: managed. See resolve_grch38_index().
DEVELOPMENT_GRCH38_VARIABLE = "BIOFLOW_GRCH38_INDEX"
#: Points MetaPhlAn at a database BioFlow did not install. GRCh38 has had
#: an equivalent since the beginning; this one exists because a container
#: or a shared filesystem may hold the ~51 GB database somewhere other
#: than under BioFlow's own database root, and copying it is not an option.
EXTERNAL_METAPHLAN_VARIABLE = "BIOFLOW_METAPHLAN_DB"

#: The six files Bowtie2 writes for one index, and the two extension flavours.
BOWTIE2_INDEX_PARTS = ("1", "2", "3", "4", "rev.1", "rev.2")
BOWTIE2_INDEX_EXTENSIONS = ("bt2", "bt2l")

CONFIG_FORMAT_VERSION = 1


# ----------------------------------------------------------------------
# Bowtie2 index validation
# ----------------------------------------------------------------------
def bowtie2_index_files(prefix: Path, extension: str) -> list[Path]:
    """The six files that make up one Bowtie2 index for a given extension."""
    return [Path(f"{prefix}.{part}.{extension}") for part in BOWTIE2_INDEX_PARTS]


def bowtie2_index_is_complete(prefix: Path) -> bool:
    """True only when every one of the six index files exists at the prefix."""
    return any(
        all(path.is_file() for path in bowtie2_index_files(prefix, extension))
        for extension in BOWTIE2_INDEX_EXTENSIONS
    )


def bowtie2_index_is_partial(prefix: Path) -> bool:
    """True when some index files exist but the set is not complete."""
    if bowtie2_index_is_complete(prefix):
        return False
    return any(
        any(path.is_file() for path in bowtie2_index_files(prefix, extension))
        for extension in BOWTIE2_INDEX_EXTENSIONS
    )


def bowtie2_index_prefix_from_file(path: Path) -> Path | None:
    """Derive an index prefix from any one of its six files.

    Lets a user point at, say, GRCh38_index.1.bt2 without having to type the
    prefix by hand. Returns None when the file is not part of a Bowtie2 index.
    """
    name = Path(path).name
    for extension in BOWTIE2_INDEX_EXTENSIONS:
        # Longest first, so "rev.2" is matched before the bare "2" inside it.
        for part in sorted(BOWTIE2_INDEX_PARTS, key=len, reverse=True):
            suffix = f".{part}.{extension}"
            if name.endswith(suffix):
                return Path(path).with_name(name[: -len(suffix)])
    return None


class ResourceState(str, Enum):
    """How a reference resource stands on this machine."""

    #: Nothing is present at any configured location.
    MISSING = "missing"
    #: Files exist but the index is not the complete six-file set.
    INCOMPLETE = "incomplete"
    #: Installed by BioFlow into its own managed store.
    MANAGED = "managed"
    #: A complete index the user explicitly configured outside the store.
    EXTERNAL = "external"
    #: Supplied by the development/test override; never treated as managed.
    DEVELOPMENT = "development"

    @property
    def usable(self) -> bool:
        """True when an analysis can actually run against this resource."""
        return self in (ResourceState.MANAGED, ResourceState.EXTERNAL, ResourceState.DEVELOPMENT)

    @property
    def label(self) -> str:
        return {
            ResourceState.MISSING: "Not installed",
            ResourceState.INCOMPLETE: "Incomplete",
            ResourceState.MANAGED: "Managed",
            ResourceState.EXTERNAL: "External",
            ResourceState.DEVELOPMENT: "Development override",
        }[self]


@dataclass(frozen=True)
class ResolvedReference:
    """Which index BioFlow will use at run time, and where it came from."""

    state: ResourceState
    #: The prefix that will actually be passed to Bowtie2, when usable.
    prefix: Path | None
    #: Where BioFlow installs its own copy. Never affected by any override.
    managed_prefix: Path

    @property
    def usable(self) -> bool:
        return self.state.usable and self.prefix is not None

    def describe(self) -> str:
        if self.prefix is None:
            return self.state.label
        return f"{self.state.label} / {self.prefix}"


def default_data_root() -> Path:
    """Return BioFlow's private data directory for this user."""
    configured = os.environ.get("BIOFLOW_DATA_DIR")
    if configured:
        return Path(configured).expanduser()
    data_home = os.environ.get("XDG_DATA_HOME")
    base = Path(data_home).expanduser() if data_home else Path.home() / ".local" / "share"
    return base / "bioflow"


@dataclass
class BioFlowConfig:
    """Resolved locations for BioFlow's private runtime and reference data."""

    data_root: Path
    database_root: Path
    default_threads: int = 4
    environment_names: dict[str, str] = field(
        default_factory=lambda: dict(DEFAULT_ENVIRONMENT_NAMES)
    )
    #: Explicitly configured external references, persisted in config.json.
    external_grch38_index: Path | None = None
    #: Which backend runs analysis commands: "native" or "container".
    #:
    #: Native is the default and stays the default. It needs nothing beyond
    #: what BioFlow installs for itself, which is the whole point of the
    #: managed Micromamba runtime; container execution adds a dependency on a
    #: runtime being present, and exists for reproducibility rather than to
    #: replace the desktop path.
    execution_backend: str = "native"
    #: The analysis image, when the container backend is selected.
    container_image: str = DEFAULT_CONTAINER_IMAGE

    # ------------------------------------------------------------------
    # Runtime locations
    # ------------------------------------------------------------------
    @property
    def binary_directory(self) -> Path:
        return self.data_root / "bin"

    @property
    def micromamba_binary(self) -> Path:
        """Path to BioFlow's private Micromamba, or an explicitly configured one."""
        configured = os.environ.get("BIOFLOW_MICROMAMBA")
        return Path(configured) if configured else self.binary_directory / "micromamba"

    @property
    def micromamba_root(self) -> Path:
        configured = os.environ.get("BIOFLOW_MAMBA_ROOT_PREFIX")
        return Path(configured) if configured else self.data_root / "micromamba-root"

    @property
    def settings_file(self) -> Path:
        return self.data_root / "config.json"

    @property
    def history_database(self) -> Path:
        configured = os.environ.get("BIOFLOW_HISTORY_DB")
        return Path(configured) if configured else self.data_root / "history.sqlite3"

    @property
    def log_directory(self) -> Path:
        return self.data_root / "logs"

    # ------------------------------------------------------------------
    # Environments
    # ------------------------------------------------------------------
    def environment_name(self, key: str) -> str:
        """Return the Micromamba environment name registered for a module key."""
        return self.environment_names.get(key) or DEFAULT_ENVIRONMENT_NAMES.get(key, key)

    def environment_prefix(self, key: str) -> Path:
        return self.micromamba_root / "envs" / self.environment_name(key)

    def environment_is_installed(self, key: str) -> bool:
        return (self.environment_prefix(key) / "bin").is_dir()

    # ------------------------------------------------------------------
    # Reference data
    # ------------------------------------------------------------------
    def database_directory(self, *parts: str) -> Path:
        return self.database_root.joinpath(*parts)

    @property
    def managed_grch38_directory(self) -> Path:
        """Where BioFlow installs the human reference. Never overridable."""
        return self.database_directory("human", "hg38")

    @property
    def managed_grch38_index_prefix(self) -> Path:
        """The one and only installation target for the GRCh38 Bowtie2 index."""
        return self.managed_grch38_directory / "GRCh38_index"

    @property
    def development_grch38_index(self) -> Path | None:
        """Development/test override, from BIOFLOW_GRCH38_INDEX."""
        configured = os.environ.get(DEVELOPMENT_GRCH38_VARIABLE)
        return Path(configured).expanduser() if configured else None

    def resolve_grch38_index(self) -> ResolvedReference:
        """Decide which GRCh38 index to use, in one deterministic order.

        1. The development/test override, when set and complete. It is reported
           as DEVELOPMENT so it can never be mistaken for a managed install.
        2. An explicitly configured, persisted external index, when complete.
        3. BioFlow's own managed installation.

        BioFlow never scans arbitrary directories looking for a reference.
        """
        managed = self.managed_grch38_index_prefix

        development = self.development_grch38_index
        if development is not None and bowtie2_index_is_complete(development):
            return ResolvedReference(ResourceState.DEVELOPMENT, development, managed)

        external = self.external_grch38_index
        if external is not None and bowtie2_index_is_complete(external):
            return ResolvedReference(ResourceState.EXTERNAL, external, managed)

        if bowtie2_index_is_complete(managed):
            return ResolvedReference(ResourceState.MANAGED, managed, managed)

        # Report a half-built or half-copied index distinctly from nothing at all.
        for candidate in (external, development, managed):
            if candidate is not None and bowtie2_index_is_partial(candidate):
                return ResolvedReference(ResourceState.INCOMPLETE, None, managed)
        return ResolvedReference(ResourceState.MISSING, None, managed)

    @property
    def grch38_index_prefix(self) -> Path:
        """The prefix host removal will pass to Bowtie2 at run time.

        Falls back to the managed location when nothing is installed yet, so
        callers always have a concrete path to report.
        """
        resolved = self.resolve_grch38_index()
        return resolved.prefix or resolved.managed_prefix

    def set_external_grch38_index(self, prefix: Path | None) -> None:
        """Record, or clear, an explicitly chosen external GRCh38 index.

        Raises ValueError when the prefix is not a complete six-file index, so a
        mistyped path can never be persisted as a working reference.
        """
        if prefix is not None:
            prefix = Path(prefix).expanduser()
            if not bowtie2_index_is_complete(prefix):
                raise ValueError(
                    f"{prefix} is not a complete Bowtie2 index: all six files "
                    f"({', '.join('.' + part for part in BOWTIE2_INDEX_PARTS)}) must exist."
                )
        self.external_grch38_index = prefix
        self.save()

    @property
    def bowtie2_executable(self) -> Path:
        """Bowtie2 inside the taxonomy environment, which MetaPhlAn drives."""
        return self.environment_prefix("taxonomy") / "bin" / "bowtie2"

    @property
    def bowtie2_memory_mapped_shim(self) -> Path:
        """A small script that runs Bowtie2 with its index memory-mapped.

        MetaPhlAn offers no way to pass extra arguments through to Bowtie2, only
        a path to the executable, so the option is added by pointing it at a
        one-line script instead. It lives in BioFlow's own bin directory beside
        Micromamba, never in a user's home.
        """
        return self.binary_directory / "bowtie2-mm"

    @property
    def metaphlan_database_directory(self) -> Path:
        """Where the MetaPhlAn database lives.

        BIOFLOW_METAPHLAN_DB overrides the managed location. The completeness
        check is applied to whatever this returns, so an override that does not
        hold a full database is reported as INCOMPLETE exactly as a broken
        managed install would be - pointing elsewhere never skips verification.
        """
        configured = os.environ.get(EXTERNAL_METAPHLAN_VARIABLE)
        if configured:
            return Path(configured).expanduser()
        return self.database_directory("metaphlan")

    @property
    def humann_database_directory(self) -> Path:
        return self.database_directory("humann")

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------
    @classmethod
    def load(cls) -> "BioFlowConfig":
        """Read saved settings, falling back to defaults for anything missing."""
        data_root = default_data_root()
        settings: dict = {}
        settings_file = data_root / "config.json"
        if settings_file.is_file():
            try:
                loaded = json.loads(settings_file.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    settings = loaded
            except (OSError, ValueError):
                settings = {}

        configured_databases = settings.get("database_root")
        database_root = (
            Path(configured_databases).expanduser()
            if configured_databases
            else data_root / "databases"
        )
        environment_names = dict(DEFAULT_ENVIRONMENT_NAMES)
        saved_names = settings.get("environment_names")
        if isinstance(saved_names, dict):
            environment_names.update(
                {key: str(value) for key, value in saved_names.items() if value}
            )

        references = settings.get("references")
        external_index = None
        if isinstance(references, dict):
            stored = references.get("grch38_index_prefix")
            if stored:
                external_index = Path(str(stored)).expanduser()

        threads = settings.get("default_threads")

        execution = settings.get("execution")
        backend, image = "native", DEFAULT_CONTAINER_IMAGE
        if isinstance(execution, dict):
            saved_backend = execution.get("backend")
            # An unrecognised value falls back to native rather than refusing to
            # start: a configuration written by a newer build must never leave
            # someone unable to run anything.
            if saved_backend in EXECUTION_BACKENDS:
                backend = saved_backend
            saved_image = execution.get("container_image")
            if saved_image:
                image = str(saved_image)

        return cls(
            data_root=data_root,
            database_root=database_root,
            default_threads=int(threads) if isinstance(threads, int) and threads > 0 else 4,
            environment_names=environment_names,
            external_grch38_index=external_index,
            execution_backend=backend,
            container_image=image,
        )

    def as_dict(self) -> dict:
        """The exact document written to config.json."""
        return {
            "format_version": CONFIG_FORMAT_VERSION,
            "database_root": str(self.database_root),
            "default_threads": self.default_threads,
            "environment_names": self.environment_names,
            "references": {
                "grch38_index_prefix": (
                    str(self.external_grch38_index) if self.external_grch38_index else None
                )
            },
            "execution": {
                "backend": self.execution_backend,
                "container_image": self.container_image,
            },
        }

    def save(self) -> bool:
        """Persist settings, skipping the write when nothing has changed.

        Returns True when the file was actually written.
        """
        payload = json.dumps(self.as_dict(), indent=2) + "\n"
        try:
            if self.settings_file.is_file():
                if self.settings_file.read_text(encoding="utf-8") == payload:
                    return False
            self.data_root.mkdir(parents=True, exist_ok=True)
            self.settings_file.write_text(payload, encoding="utf-8")
        except OSError:
            # A read-only or unwritable data directory must not crash startup;
            # the preflight check reports it separately.
            return False
        return True

    def ensure_stored(self) -> bool:
        """Write config.json the first time BioFlow runs, then leave it alone."""
        if self.settings_file.is_file():
            return False
        return self.save()


_CONFIG: BioFlowConfig | None = None


def get_config() -> BioFlowConfig:
    """Return the process-wide configuration, loading it on first use."""
    global _CONFIG
    if _CONFIG is None:
        _CONFIG = BioFlowConfig.load()
        _CONFIG.ensure_stored()
    return _CONFIG


def reload_config() -> BioFlowConfig:
    """Discard the cached configuration and read it again from disk."""
    global _CONFIG
    _CONFIG = None
    return get_config()
