"""What this process may actually use, rather than what the machine has fitted.

BioFlow's memory policy was measured on bare metal, where `/proc/meminfo`
answers the question "how much memory is there" and that is also the answer to
"how much may I use". Inside a container the two come apart: the kernel reports
the host's memory to a process confined to a fraction of it, and a policy that
believes the larger number chooses to load a 33 GB index into a cgroup that
will kill it for trying.

Every value here is a *ceiling*, not a reading of current use. Ceilings hold
still for the life of a run, which is what the checkpoint fingerprint needs:
`should_memory_map()` decides part of MetaPhlAn's command line, so a number that
drifted between two stages would rewrite the command and silently invalidate
results that are still good. Current usage - MemAvailable, memory.current - is
deliberately not consulted here for that reason.
"""

from pathlib import Path
import os


CGROUP_ROOT = Path("/sys/fs/cgroup")
PROC_MEMINFO = Path("/proc/meminfo")
PROC_SELF_CGROUP = Path("/proc/self/cgroup")

#: cgroup v1 writes "no limit" as the largest page-aligned value the counter can
#: hold rather than as a word, and the exact figure depends on the page size.
#: Anything at or above a pebibyte is not a real container limit; it is that
#: sentinel, or a limit so large it cannot constrain anything BioFlow runs.
UNLIMITED_THRESHOLD_BYTES = 1024 ** 5


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def meminfo_bytes(field: str) -> int:
    """One `/proc/meminfo` field in bytes, or 0 when it cannot be read."""
    try:
        for line in PROC_MEMINFO.read_text(encoding="utf-8").splitlines():
            if line.startswith(f"{field}:"):
                return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        return 0
    return 0


def _own_cgroup_path() -> str:
    """This process's cgroup v2 path, relative to the hierarchy root.

    Inside a cgroup namespace this is "/" and the limit files sit directly under
    the mount point. Outside one - the ordinary desktop case - it names the
    slice the process was launched into.
    """
    for line in _read(PROC_SELF_CGROUP).splitlines():
        parts = line.split(":", 2)
        if len(parts) == 3 and parts[0] == "0":
            return parts[2]
    return "/"


def _cgroup_v2_directories() -> list[Path]:
    """The process's own cgroup directory and every ancestor up to the root.

    A limit set on an ancestor binds a descendant that sets none of its own, so
    the effective ceiling is the smallest limit found anywhere on this chain.
    """
    relative = _own_cgroup_path().strip("/")
    directories = [CGROUP_ROOT]
    current = CGROUP_ROOT
    for part in relative.split("/") if relative else []:
        current = current / part
        directories.append(current)
    return directories


def cgroup_memory_limit_bytes() -> int:
    """The tightest cgroup memory ceiling on this process, or 0 for none."""
    limits: list[int] = []

    for directory in _cgroup_v2_directories():
        raw = _read(directory / "memory.max")
        if not raw or raw == "max":
            continue
        try:
            value = int(raw)
        except ValueError:
            continue
        if 0 < value < UNLIMITED_THRESHOLD_BYTES:
            limits.append(value)

    if not limits:
        raw = _read(CGROUP_ROOT / "memory" / "memory.limit_in_bytes")
        try:
            value = int(raw)
        except ValueError:
            value = 0
        if 0 < value < UNLIMITED_THRESHOLD_BYTES:
            limits.append(value)

    return min(limits) if limits else 0


def memory_limit_bytes() -> int:
    """Memory this process may use, in bytes, or 0 when nothing can be read.

    Installed RAM where there is no cgroup limit, which is every ordinary
    desktop run and keeps the measured policy behaving exactly as it always
    has. Where a limit exists it wins, because exceeding it is fatal in a way
    that exceeding installed RAM is not: the cgroup kills without swapping.
    """
    installed = meminfo_bytes("MemTotal")
    limit = cgroup_memory_limit_bytes()
    if installed and limit:
        return min(installed, limit)
    return limit or installed


def cgroup_memory_headroom_bytes() -> int:
    """Room left inside the tightest cgroup memory ceiling, or 0 for none.

    Unlike every other figure in this module this one moves, because it is the
    only one asked about *now* rather than about the ceiling. That is fine where
    it is used - warning someone before a run that the machine is already full -
    and it is why this must never reach `should_memory_map()`: a value that
    drifted between two stages would rewrite MetaPhlAn's command line and
    invalidate a checkpoint that is still good.
    """
    headroom: list[int] = []
    for directory in _cgroup_v2_directories():
        raw = _read(directory / "memory.max")
        if not raw or raw == "max":
            continue
        try:
            limit = int(raw)
            used = int(_read(directory / "memory.current") or 0)
        except ValueError:
            continue
        if 0 < limit < UNLIMITED_THRESHOLD_BYTES:
            headroom.append(max(0, limit - used))
    return min(headroom) if headroom else 0


def available_memory_bytes() -> int:
    """Memory free for a new process right now, or 0 when it cannot be read.

    Installed RAM is the wrong question for an aligner: a machine with 14 GB
    fitted but an editor and a browser open cannot spare the 10 GB MetaPhlAn
    wants, and the kernel resolves that by killing something. Inside a cgroup
    the free memory the kernel reports belongs to the host, so whichever of the
    two is tighter is the one that will actually stop the run.
    """
    free = meminfo_bytes("MemAvailable")
    headroom = cgroup_memory_headroom_bytes()
    if free and headroom:
        return min(free, headroom)
    return headroom or free


def cgroup_cpu_quota() -> int:
    """Whole CPUs this process's cgroup allows, or 0 when it is unrestricted.

    Rounded down deliberately. A fractional allowance cannot run two threads at
    full speed, and the cost of overestimating threads is not a slower run but
    the page-cache collapse that made MetaPhlAn cap itself at one thread.
    """
    for directory in _cgroup_v2_directories():
        raw = _read(directory / "cpu.max")
        if not raw:
            continue
        parts = raw.split()
        if len(parts) != 2 or parts[0] == "max":
            continue
        try:
            quota, period = int(parts[0]), int(parts[1])
        except ValueError:
            continue
        if quota > 0 and period > 0:
            return max(1, quota // period)

    try:
        quota = int(_read(CGROUP_ROOT / "cpu" / "cpu.cfs_quota_us"))
        period = int(_read(CGROUP_ROOT / "cpu" / "cpu.cfs_period_us"))
    except ValueError:
        return 0
    if quota > 0 and period > 0:
        return max(1, quota // period)
    return 0


def usable_cpus() -> int:
    """CPUs this process may actually schedule on. Never less than one.

    `os.cpu_count()` answers a different question - how many the machine has -
    and is wrong in three situations BioFlow meets: a cgroup CPU quota, a
    cpuset, and a run started under `taskset`. Scheduling affinity covers the
    last two natively, so this is a correction on bare metal as well as in a
    container.
    """
    try:
        affinity = len(os.sched_getaffinity(0))
    except (AttributeError, OSError):
        affinity = os.cpu_count() or 1
    quota = cgroup_cpu_quota()
    if quota:
        return max(1, min(affinity, quota))
    return max(1, affinity)
