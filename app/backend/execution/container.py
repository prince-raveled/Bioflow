"""Run a stage's command inside an OCI container instead of a local environment.

A sibling to EnvironmentResolver, not a replacement: it answers the same two
questions - may this run, and what is the command - so everything above it
(Stage, Workspace, CommandRunner, the validators, the checkpoint) is unchanged
and unaware. Native execution remains the default and is untouched.

The image carries the same Micromamba environments under the same names, so the
inner command is identical in both backends and only the prefix differs:

    native      micromamba run -r <root> -n bioflow-qc fastqc ...
    container   podman run <flags> IMAGE micromamba run -r /opt/conda -n bioflow-qc fastqc ...
                                          |___________ identical from here ___________|

That is what makes a container result comparable with a native one argument by
argument, and what lets one set of stage tests cover both.

Paths are mounted at their own location - host /data/reads becomes /data/reads
inside - so no argument is rewritten. A translation layer would have to find
every path in every command, and a path it missed would not fail loudly; it
would point somewhere plausible and wrong. Identity mounts also keep logs, tool
error messages and the paths tools embed in their reports meaningful to whoever
reads them.
"""

from dataclasses import dataclass, field
from pathlib import Path
import os
import shutil
import subprocess

from backend.config import BioFlowConfig, get_config
from backend.execution.environment import MissingBackend
from backend.setup.manager import DATABASE_PREFIX, SetupManager
from backend.setup.registry import database_specs


#: Runtimes this understands, in the order it prefers them.
#:
#: Podman first because it is what Fedora and RHEL ship, it needs no daemon, and
#: rootless it needs no group membership. Docker's group is equivalent to root -
#: any member can mount the host filesystem into a privileged container - which
#: is a large thing to require in order to run a QC pipeline.
RUNTIMES = ("podman", "docker")

#: The default image. Overridable through configuration; pinned by digest in the
#: run record so a result names the exact bytes that produced it.
DEFAULT_IMAGE = "localhost/bioflow-tools:0.1.0"

#: Where the image keeps its environments and the memory-mapping shim.
CONTAINER_MAMBA_ROOT = Path("/opt/conda")
CONTAINER_SHIM = Path("/opt/bioflow/bin/bowtie2-mm")

#: Directories that must never be identity-mounted over.
#:
#: The container's own filesystem lives at these paths, and shadowing one would
#: replace the tools with the host's. /tmp is on the list for a subtler reason
#: found by running it: the container gets a writable tmpfs at /tmp, and that
#: tmpfs hides anything mounted beneath it. The mount succeeds and is empty.
SYSTEM_PATHS = (
    "/", "/bin", "/boot", "/dev", "/etc", "/lib", "/lib32", "/lib64", "/libx32",
    "/opt", "/proc", "/root", "/run", "/sbin", "/sys", "/tmp", "/usr", "/var",
)

#: Size of the writable tmpfs the container gets. MetaPhlAn's paired subsampling
#: writes temporary FASTQs there, so it has to be more than a token amount.
TMPFS_SIZE = "4g"

#: Runtime exit codes that describe the runtime rather than the tool. Passing
#: these through as though a tool had returned them would blame the wrong thing.
RUNTIME_EXIT_CODES = {
    125: "the container runtime itself failed to start the container",
    126: "the container command was found but could not be executed",
    127: "the container command was not found inside the image",
}


def detect_runtime(preferred: str | None = None) -> str | None:
    """The container runtime to use, or None when none is installed."""
    if preferred:
        return preferred if shutil.which(preferred) else None
    for candidate in RUNTIMES:
        if shutil.which(candidate):
            return candidate
    return None


def runtime_available(runtime: str) -> tuple[bool, str]:
    """Whether the runtime can actually start containers, and why not."""
    try:
        result = subprocess.run(
            [runtime, "info", "--format", "{{.Host.Arch}}"],
            capture_output=True, text=True, timeout=60,
        )
    except (OSError, subprocess.SubprocessError) as error:
        return False, str(error)
    if result.returncode == 0:
        return True, ""
    detail = (result.stderr or result.stdout).strip().splitlines()
    message = detail[-1] if detail else f"{runtime} info exited {result.returncode}"
    if "permission denied" in message.lower() or "docker.sock" in message.lower():
        message = (
            f"{message}. Docker needs its daemon running and your user in the "
            f"'docker' group, which is equivalent to root on this machine. "
            f"Installing Podman avoids both."
        )
    return False, message


def describe_runtime_exit(code: int) -> str:
    """Explain an exit status that belongs to the runtime, not the tool."""
    return RUNTIME_EXIT_CODES.get(code, "")


def is_system_path(path: Path) -> bool:
    """Whether mounting here would shadow the container's own filesystem."""
    text = str(path)
    return any(text == system for system in SYSTEM_PATHS)


def mountable_ancestor(path: Path) -> Path | None:
    """The directory to mount so that `path` is reachable inside the container.

    The nearest existing directory at or above the path. Outputs do not exist
    yet and Bowtie2's `%` template never will, so the parent is what matters.
    """
    candidate = path if path.is_dir() else path.parent
    while True:
        if candidate.is_dir():
            return candidate
        if candidate.parent == candidate:
            return None
        candidate = candidate.parent


def collapse(paths: set[Path]) -> list[Path]:
    """Drop any path already covered by another, and order them stably."""
    ordered = sorted(paths, key=lambda item: len(item.parts))
    kept: list[Path] = []
    for path in ordered:
        if not any(path == parent or parent in path.parents for parent in kept):
            kept.append(path)
    return kept


@dataclass
class MountPlan:
    """What the container may see, and what it may write to."""

    read_only: list[Path] = field(default_factory=list)
    writable: list[Path] = field(default_factory=list)

    def arguments(self) -> list[str]:
        arguments: list[str] = []
        for path in self.writable:
            arguments += ["-v", f"{path}:{path}:rw"]
        for path in self.read_only:
            arguments += ["-v", f"{path}:{path}:ro"]
        return arguments


class ContainerResolver:
    """Turn a tool invocation into a container command.

    Presents the same interface as EnvironmentResolver so CommandRunner needs no
    knowledge of which one it holds.
    """

    backend = "container"

    def __init__(
        self,
        config: BioFlowConfig | None = None,
        context=None,
        image: str = "",
        runtime: str | None = None,
        cidfile_directory: Path | None = None,
    ):
        self.config = config or get_config()
        self.manager = SetupManager(self.config)
        self.context = context
        self.image = image or DEFAULT_IMAGE
        self.runtime = detect_runtime(runtime)
        self.cidfile_directory = cidfile_directory
        #: Containers started but not yet known to have exited, so a cancel can
        #: guarantee cleanup even where the client was killed outright.
        self._cidfiles: list[Path] = []

    # ------------------------------------------------------------------
    def check(self, environment_key: str, databases: tuple[str, ...] = ()) -> list[str]:
        """Setup components that must be present before running.

        The environments live in the image, so unlike the native resolver this
        does not ask whether Micromamba or an environment is installed. It does
        ask about databases, because those are mounted from the host either way.
        """
        missing: list[str] = []
        specifications = database_specs()
        for database_key in databases:
            specification = specifications.get(database_key)
            if specification and not self.manager.database_installed(specification):
                missing.append(f"{DATABASE_PREFIX}{database_key}")
        return missing

    def describe(self, components: list[str]) -> str:
        specifications = database_specs()
        names = []
        for component in components:
            if component.startswith(DATABASE_PREFIX):
                key = component[len(DATABASE_PREFIX):]
                specification = specifications.get(key)
                names.append(specification.title if specification else key)
            else:
                names.append(component)
        return ", ".join(names)

    def require(self, environment_key: str, databases: tuple[str, ...] = ()) -> None:
        """Raise MissingBackend unless a container run can actually start."""
        if self.runtime is None:
            raise MissingBackend(
                message=(
                    "Container execution needs Podman or Docker, and neither is "
                    "installed. Install Podman, or switch back to native "
                    "execution in Setup & Resources."
                ),
                components=["container-runtime"],
            )
        usable, reason = runtime_available(self.runtime)
        if not usable:
            raise MissingBackend(
                message=f"{self.runtime} is installed but cannot run containers: {reason}",
                components=["container-runtime"],
            )
        if not self.image_present():
            raise MissingBackend(
                message=(
                    f"The analysis image {self.image} is not available. Build it "
                    f"with docker/build.sh, or pull it, before running in "
                    f"container mode."
                ),
                components=["container-image"],
            )
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
    def image_present(self) -> bool:
        if self.runtime is None:
            return False
        try:
            result = subprocess.run(
                [self.runtime, "image", "exists", self.image],
                capture_output=True, timeout=60,
            )
        except (OSError, subprocess.SubprocessError):
            return False
        return result.returncode == 0

    def image_digest(self) -> str:
        """The image's digest, which is what a run record should name.

        A tag can be moved to different bytes; a digest cannot. Returns the tag
        when no digest is available, so a locally built image still identifies
        itself rather than identifying nothing.
        """
        if self.runtime is None:
            return self.image
        try:
            result = subprocess.run(
                [self.runtime, "image", "inspect", self.image, "--format", "{{.Digest}}"],
                capture_output=True, text=True, timeout=60,
            )
        except (OSError, subprocess.SubprocessError):
            return self.image
        digest = result.stdout.strip()
        return digest if result.returncode == 0 and digest else self.image

    # ------------------------------------------------------------------
    def paths_in(self, command: list[str]) -> set[Path]:
        """Absolute host paths a command refers to.

        Read from the arguments rather than from a list kept alongside them, so
        a stage that starts naming a new file cannot be forgotten here. Anything
        inside the image is skipped: mounting the host over /opt would replace
        the tools with whatever the host happens to have there.
        """
        found: set[Path] = set()
        for argument in command:
            # Unquote first. Bowtie2's --un-conc-gz value is shell-quoted when
            # the path needs it, for Bowtie2's own sh -c, so a paired output
            # directory containing a space arrives here as '…' and does not
            # start with a slash. Testing before stripping skipped exactly that
            # case, leaving the run without the mount it writes into.
            text = argument.strip("'\"")
            if not text.startswith("/"):
                continue
            path = Path(text)
            if str(path).startswith(("/opt/conda", "/opt/bioflow", "/dev/null")):
                continue
            found.add(path)
        return found

    def mount_plan(self, command: list[str]) -> MountPlan:
        """Everything this command must see, and what it may write to."""
        writable: set[Path] = set()
        read_only: set[Path] = set()

        context = self.context
        if context is not None:
            root = mountable_ancestor(Path(context.workspace.root))
            if root:
                writable.add(root)
            for reference in (context.host_index_prefix, context.metaphlan_database):
                if reference is None:
                    continue
                directory = mountable_ancestor(Path(reference))
                if directory:
                    read_only.add(directory)

        # A root that lands on a system path is fatal: the user's data really
        # is somewhere it cannot be reached from, and mounting over /usr would
        # replace the analysis tools with whatever the host keeps there.
        for path in read_only | writable:
            if is_system_path(path):
                raise MissingBackend(
                    message=(
                        f"Cannot run in a container with data at {path}: that "
                        f"path is part of the container's own filesystem and "
                        f"mounting over it would replace the analysis tools. "
                        f"Move the data under your home directory, /mnt or "
                        f"/data, or run natively."
                    ),
                    components=[],
                )

        # A system path named in the command itself is a different thing: it
        # refers to the container's own filesystem, which is already there.
        # /tmp is the tmpfs the container was given, /usr/bin/python is the
        # image's interpreter. Mounting the host over either would be wrong,
        # and refusing the run over it would be wrong too - so they are simply
        # not mounted.
        for path in self.paths_in(command):
            directory = mountable_ancestor(path)
            if directory is None or is_system_path(directory):
                continue
            if any(directory == item or item in directory.parents for item in writable):
                continue
            read_only.add(directory)

        return MountPlan(
            read_only=collapse(read_only - writable),
            writable=collapse(writable),
        )

    # ------------------------------------------------------------------
    def security_arguments(self) -> list[str]:
        """The posture every analysis container runs under.

        Analysis needs no network, no capabilities and no writable root: a FASTQ
        is untrusted input from a collaborator or a public archive, and with no
        network interface a compromised tool has nowhere to send anything. It
        also makes MetaPhlAn's --offline true rather than merely requested.

        SELinux is dropped for the container rather than relabelled. Fedora and
        RHEL enforce labels on bind mounts, and the usual answer - :z or :Z on
        each mount - rewrites the host directory's label. That would walk a
        ~51 GB reference database, and :Z applies a private label that would
        leave that database unreadable by the native backend. Confinement is
        turned off for this container instead; no host label is touched.
        """
        arguments = [
            "--rm",
            "--network=none",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            "--read-only",
            "--tmpfs", f"/tmp:rw,size={TMPFS_SIZE},exec",
        ]
        if self.runtime == "podman":
            # Files land owned by the invoking user rather than by root.
            arguments += ["--userns=keep-id"]
        else:
            arguments += ["--user", f"{os.getuid()}:{os.getgid()}"]
        arguments += ["--security-opt", "label=disable"]
        return arguments

    def cidfile_for(self, environment_key: str) -> Path | None:
        """Where the runtime should record this container's id.

        Terminating the client normally stops the container, but a client that
        is killed outright cannot. The id on disk is what lets a cancel clean up
        regardless.
        """
        if self.cidfile_directory is None:
            return None
        self.cidfile_directory.mkdir(parents=True, exist_ok=True)
        path = self.cidfile_directory / f"{environment_key}-{os.getpid()}-{len(self._cidfiles)}.cid"
        # The runtime refuses to start when the file already exists.
        path.unlink(missing_ok=True)
        self._cidfiles.append(path)
        return path

    def cleanup_containers(self) -> list[str]:
        """Force-remove any container this resolver started that still exists."""
        removed: list[str] = []
        if self.runtime is None:
            return removed
        for cidfile in list(self._cidfiles):
            try:
                identifier = cidfile.read_text(encoding="utf-8").strip()
            except OSError:
                continue
            if not identifier:
                continue
            try:
                result = subprocess.run(
                    [self.runtime, "rm", "-f", "--ignore", identifier],
                    capture_output=True, text=True, timeout=60,
                )
                if result.returncode == 0:
                    removed.append(identifier[:12])
            except (OSError, subprocess.SubprocessError):
                continue
            finally:
                cidfile.unlink(missing_ok=True)
        self._cidfiles.clear()
        return removed

    # ------------------------------------------------------------------
    def environment_name(self, environment_key: str) -> str:
        """The environment's name inside the image.

        The same name the native installation uses, which is what keeps the
        inner command identical between the two backends.
        """
        return self.config.environment_name(environment_key)

    def inner_command(self, environment_key: str, command: list[str]) -> list[str]:
        """The command as it runs inside the container.

        Byte-identical to the native resolver's arguments except for the
        Micromamba root, which is fixed inside the image.
        """
        return [
            "micromamba", "run",
            "-r", str(CONTAINER_MAMBA_ROOT),
            "-n", self.environment_name(environment_key),
            *command,
        ]

    def resolve(self, environment_key: str, command: list[str]) -> tuple[str, list[str]]:
        """Build the container invocation that runs `command` in the image."""
        self.require(environment_key)
        plan = self.mount_plan(command)

        arguments = ["run", *self.security_arguments()]
        cidfile = self.cidfile_for(environment_key)
        if cidfile is not None:
            arguments += ["--cidfile", str(cidfile)]
        # HOME must be somewhere writable: tools that consult it fail on a
        # read-only root, and /tmp is the one writable place a container has.
        arguments += ["-e", "HOME=/tmp", "-e", "TMPDIR=/tmp"]
        arguments += plan.arguments()
        arguments += [self.image, *self.inner_command(environment_key, command)]
        return self.runtime, arguments
