"""System checks run before BioFlow downloads or installs anything."""

from dataclasses import dataclass
from pathlib import Path
import os
import shutil
import socket
import sys
import time

from backend.config import BioFlowConfig
from backend.resources import (
    available_memory_bytes as detect_available_memory,
    meminfo_bytes,
    memory_limit_bytes,
    usable_cpus,
)
from backend.setup.bootstrap import human_bytes


@dataclass(frozen=True)
class SystemCheck:
    """One preflight result shown on the Setup page."""

    name: str
    passed: bool
    detail: str
    #: A failed advisory warns the user; a failed requirement blocks installation.
    blocking: bool = False


def _nearest_existing(path: Path) -> Path:
    for candidate in [path, *path.parents]:
        if candidate.exists():
            return candidate
    return Path("/")


#: Above this, a reported free-space figure is treated as unverifiable rather
#: than trusted. Thin-provisioned, virtual and network filesystems report the
#: volume's nominal size, not what the backing store can actually supply; one
#: clean-machine install saw 950 GB reported against a ~97 GB disk.
IMPLAUSIBLE_FREE_BYTES = 2 * 1024 ** 4  # 2 TB


def free_bytes(path: Path) -> int:
    """Free space on the filesystem that will hold the given path."""
    return shutil.disk_usage(_nearest_existing(path)).free


#: Filesystems that report a volume's nominal size rather than what the backing
#: store can supply: network mounts, container overlays, and host passthroughs.
UNVERIFIABLE_FILESYSTEMS = frozenset({
    "9p", "drvfs", "virtiofs", "cifs", "smb3", "nfs", "nfs4",
    "overlay", "fuseblk", "vboxsf",
})


def running_under_wsl() -> bool:
    """True on WSL, where even ext4 sits inside a sparse, growable disk image.

    The filesystem type looks ordinary there, so type alone cannot reveal the
    problem: a WSL2 volume commonly reports its nominal maximum while the
    Windows host has far less actually free.
    """
    if os.environ.get("WSL_DISTRO_NAME"):
        return True
    try:
        return "microsoft" in Path("/proc/version").read_text(encoding="utf-8").lower()
    except OSError:
        return False


def filesystem_type(path: Path) -> str:
    """The mounted filesystem type for a path, or "" when it cannot be read."""
    target = _nearest_existing(path).resolve()
    best, best_type = "", ""
    try:
        for line in Path("/proc/mounts").read_text(encoding="utf-8").splitlines():
            parts = line.split()
            if len(parts) < 3:
                continue
            mount_point, mount_type = parts[1], parts[2]
            # The longest matching mount point is the one actually in effect.
            if str(target) == mount_point or str(target).startswith(mount_point.rstrip("/") + "/"):
                if len(mount_point) >= len(best):
                    best, best_type = mount_point, mount_type
    except OSError:
        return ""
    return best_type


def free_space_is_trustworthy(free: int, path: Path) -> bool:
    """Whether a reported free-space figure can be relied on to gate a download.

    This does not try to compute the true figure: there is no portable way to
    see through a thin-provisioned volume. It only decides whether BioFlow is
    entitled to assert the number it was given.
    """
    if free >= IMPLAUSIBLE_FREE_BYTES:
        return False
    if running_under_wsl():
        return False
    if filesystem_type(path) in UNVERIFIABLE_FILESYSTEMS:
        return False
    total = shutil.disk_usage(_nearest_existing(path)).total
    # A volume claiming more free space than it has capacity for is incoherent.
    return free <= total


#: Floor for running BioFlow at all, when no component asks for more.
BASELINE_MEMORY_BYTES = 8 * 1024 ** 3


def available_memory_bytes() -> int:
    """Memory free for a new process now, or 0 when it cannot be determined.

    Installed RAM is the wrong question for an aligner: a machine with 14 GB
    fitted but an editor and a browser open cannot spare the 10 GB MetaPhlAn
    wants, and the kernel resolves that by killing something.
    """
    return detect_available_memory()


def swap_bytes() -> tuple[int, int]:
    """Total swap, and how much of it is zram, in bytes.

    The two are not interchangeable. zram is compressed memory living in RAM:
    it buys room only to the extent the pages compress, and the largest thing
    BioFlow runs - MetaPhlAn's ~7 GB marker table - compresses badly. Swap on a
    disk is where a page can actually go when RAM is full.

    This distinction is not academic. A 14 GB machine with 8 GB of zram and no
    disk swap had MetaPhlAn killed at 6.8 GB twice; the same machine, same
    command, with a 16 GB swapfile added, ran it to completion.
    """
    total = meminfo_bytes("SwapTotal")

    compressed = 0
    try:
        for line in Path("/proc/swaps").read_text(encoding="utf-8").splitlines()[1:]:
            parts = line.split()
            if len(parts) >= 3 and "zram" in parts[0]:
                compressed += int(parts[2]) * 1024
    except (OSError, ValueError, IndexError):
        compressed = 0
    return total, compressed


def total_memory_bytes() -> int:
    """Memory this machine may give a run, or 0 when it cannot be determined.

    Delegated so that preflight and the taxonomy stage cannot disagree about
    how much memory there is: they previously answered the question with two
    different implementations, and a cgroup ceiling was invisible to both.
    """
    return memory_limit_bytes()


#: Connectivity is re-tested at most this often, so toggling a checkbox in the
#: Setup page never blocks the interface on a socket timeout.
NETWORK_CACHE_SECONDS = 30.0
_network_cache: tuple[float, bool] | None = None


def has_network(
    host: str = "conda.anaconda.org",
    port: int = 443,
    timeout: float = 4.0,
    use_cache: bool = True,
    attempts: int = 3,
) -> bool:
    """Check that the package and database hosts are reachable."""
    global _network_cache
    now = time.monotonic()
    if use_cache and _network_cache and now - _network_cache[0] < NETWORK_CACHE_SECONDS:
        return _network_cache[1]
    # A single dropped packet should not block a multi-gigabyte install, so a
    # brief outage is retried before the check is reported as failed.
    reachable = False
    for attempt in range(attempts):
        try:
            with socket.create_connection((host, port), timeout=timeout):
                reachable = True
                break
        except OSError:
            if attempt + 1 < attempts:
                time.sleep(1.5)
    _network_cache = (time.monotonic(), reachable)
    return reachable



#: Room a desktop session needs alongside the analysis. Below this much spare
#: RAM the kernel has to be able to push something to disk, or it kills instead.
#: Measured: a 15 GB machine running a 10 GB component - 5 GB spare - was killed
#: with zram-only swap, and completed once a disk swapfile was added.
DESKTOP_RESERVE_BYTES = 6 * 1024 ** 3


def swap_file_instructions(size_bytes: int, path: Path) -> str:
    """The commands that actually add swap on this machine's filesystem.

    Not the same everywhere, which is why "add a swap file" on its own is poor
    advice. A btrfs swap file has to be made with btrfs' own tool: it must be
    nocow, uncompressed and unsnapshotted, and a file produced by fallocate is
    none of those and will be refused. The size is rounded up to a whole number
    of gigabytes because that is how these commands take it.
    """
    gigabytes = max(1, -(-size_bytes // 1024 ** 3))
    filesystem = filesystem_type(path)
    if filesystem == "btrfs":
        create = f"sudo btrfs filesystem mkswapfile --size {gigabytes}G /swapfile"
    else:
        create = (
            f"sudo fallocate -l {gigabytes}G /swapfile && "
            f"sudo chmod 600 /swapfile && sudo mkswap /swapfile"
        )
    return (
        f"{create}; sudo swapon /swapfile; "
        f"echo '/swapfile none swap defaults 0 0' | sudo tee -a /etc/fstab"
    )


def _swap_check(memory: int, required: int, swap_total: int, swap_compressed: int) -> SystemCheck:
    """Whether the machine can survive a component asking for most of its RAM.

    RAM alone is enough when there is room to spare after the largest component
    has taken its share. When there is not, the kernel needs somewhere to put
    cold pages, and only swap on a disk counts: zram is compressed memory, and
    the marker table that drives this requirement does not compress well.
    """
    disk_swap = max(0, swap_total - swap_compressed)
    spare = memory - required
    if spare >= DESKTOP_RESERVE_BYTES:
        return SystemCheck(
            "Swap", True, f"{human_bytes(spare)} of RAM to spare; swap is not needed"
        )
    if disk_swap >= required:
        return SystemCheck(
            "Swap", True, f"{human_bytes(disk_swap)} on disk, enough to cover a "
                          f"{human_bytes(required)} component"
        )
    zram_note = (
        f" {human_bytes(swap_compressed)} of zram does not count: it is compressed "
        f"RAM, and this data compresses poorly."
        if swap_compressed else ""
    )
    return SystemCheck(
        "Swap",
        False,
        f"only {human_bytes(spare)} of RAM to spare and {human_bytes(disk_swap)} of "
        f"swap on disk.{zram_note} A {human_bytes(required)} component can be killed "
        f"here. Add a swap file — the last line makes it survive a reboot, which "
        f"a swap file added by hand does not:  "
        + swap_file_instructions(required, Path.home()),
    )


def run_preflight(
    config: BioFlowConfig,
    required_bytes: int = 0,
    use_network_cache: bool = True,
    required_memory_bytes: int = 0,
) -> list[SystemCheck]:
    """Return every system check, including a disk test for a planned install."""
    checks: list[SystemCheck] = []

    version = sys.version_info
    checks.append(
        SystemCheck(
            "Python runtime",
            version >= (3, 10),
            f"Python {version.major}.{version.minor}.{version.micro}",
            blocking=True,
        )
    )

    cores = usable_cpus()
    checks.append(SystemCheck("CPU cores", cores >= 2, f"{cores} core(s) available"))

    memory = total_memory_bytes()
    swap_total, swap_compressed = swap_bytes()
    needed_memory = max(required_memory_bytes, BASELINE_MEMORY_BYTES)
    if memory == 0:
        memory_detail = "Could not be determined"
    elif memory >= needed_memory:
        memory_detail = f"{human_bytes(memory)} installed"
        if swap_total:
            memory_detail += f", {human_bytes(swap_total)} swap"
    else:
        # Name the component's requirement rather than a generic floor: an
        # alignment killed by the kernel part-way through looks like a crash,
        # and the user has no way to connect it back to their RAM.
        memory_detail = (
            f"{human_bytes(memory)} installed, but the selected components "
            f"need about {human_bytes(needed_memory)} to run"
        )
    checks.append(
        SystemCheck("Memory", memory == 0 or memory >= needed_memory, memory_detail)
    )
    if required_memory_bytes and memory:
        checks.append(_swap_check(memory, required_memory_bytes, swap_total, swap_compressed))

    writable = True
    try:
        config.data_root.mkdir(parents=True, exist_ok=True)
        probe = config.data_root / ".bioflow-write-test"
        probe.write_text("", encoding="utf-8")
        probe.unlink()
    except OSError as error:
        writable = False
        detail = f"{config.data_root} is not writable ({error.strerror})"
    else:
        detail = f"{config.data_root}"
    checks.append(SystemCheck("Data directory", writable, detail, blocking=True))

    available = free_bytes(config.database_root)
    trustworthy = free_space_is_trustworthy(available, config.database_root)
    if required_bytes:
        # Reference data unpacks alongside its download, so ask for headroom.
        needed = int(required_bytes * 1.15)
        if trustworthy:
            detail = f"{human_bytes(available)} free, about {human_bytes(needed)} needed"
        else:
            # Say so plainly rather than asserting a figure that cannot be
            # stood behind.
            detail = (
                f"could not confirm free space on this filesystem "
                f"(it reports {human_bytes(available)}); about {human_bytes(needed)} "
                f"is needed - check the underlying disk before continuing"
            )
        checks.append(
            SystemCheck("Disk space", available >= needed, detail, blocking=True)
        )
    elif trustworthy:
        checks.append(
            SystemCheck("Disk space", True, f"{human_bytes(available)} free at {config.database_root}")
        )
    else:
        checks.append(
            SystemCheck(
                "Disk space",
                True,
                f"reported {human_bytes(available)} free at {config.database_root}, "
                f"which this filesystem cannot be relied on to confirm",
            )
        )

    online = has_network(use_cache=use_network_cache)
    checks.append(
        SystemCheck(
            "Internet access",
            online,
            "Package and database hosts are reachable" if online else "No connection to conda.anaconda.org",
            blocking=True,
        )
    )

    checks.append(container_check(config))
    return checks


def container_check(config: BioFlowConfig) -> SystemCheck:
    """Whether the selected execution backend can actually run.

    Reported for both backends rather than only when containers are selected:
    somebody deciding whether to switch wants to know what is available before
    switching, and somebody who has switched wants to find out here rather than
    part-way through an analysis.

    Never blocking. Native execution needs none of this, and a container run
    that cannot start fails with a message naming what to install - failing
    setup outright would stop someone installing the databases they need for
    the native path.
    """
    # Imported here: preflight is reached from the setup CLI on machines that
    # may have no container support at all, and this must not make that path
    # depend on the container module loading.
    from backend.execution.container import ContainerResolver, detect_runtime

    runtime = detect_runtime()
    selected = config.execution_backend == "container"

    if runtime is None:
        return SystemCheck(
            "Container execution",
            not selected,
            (
                "Selected, but no container runtime is installed. Install Podman, "
                "or switch to running on this machine in Setup & Resources."
                if selected
                else "Not installed. Analysis runs on this machine, which needs nothing further."
            ),
        )

    resolver = ContainerResolver(config=config, image=config.container_image)
    if not resolver.image_present():
        return SystemCheck(
            "Container execution",
            not selected,
            (
                f"{runtime} is installed, but the image {config.container_image} "
                f"is not available. Build it with docker/build.sh."
                if selected
                else f"{runtime} is available; the image {config.container_image} is not built yet."
            ),
        )

    where = "Analysis runs in" if selected else "Available:"
    return SystemCheck(
        "Container execution",
        True,
        f"{where} {config.container_image} using {runtime}",
    )


def blocking_failures(checks: list[SystemCheck]) -> list[SystemCheck]:
    return [check for check in checks if check.blocking and not check.passed]
