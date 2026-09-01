"""System checks run before BioFlow downloads or installs anything."""

from dataclasses import dataclass
from pathlib import Path
import os
import shutil
import socket
import sys
import time

from backend.config import BioFlowConfig
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


def free_bytes(path: Path) -> int:
    """Free space on the filesystem that will hold the given path."""
    return shutil.disk_usage(_nearest_existing(path)).free


def total_memory_bytes() -> int:
    """Installed RAM, or 0 when it cannot be determined."""
    try:
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    except (ValueError, OSError, AttributeError):
        return 0


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


def run_preflight(
    config: BioFlowConfig, required_bytes: int = 0, use_network_cache: bool = True
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

    cores = os.cpu_count() or 1
    checks.append(SystemCheck("CPU cores", cores >= 2, f"{cores} core(s) available"))

    memory = total_memory_bytes()
    checks.append(
        SystemCheck(
            "Memory",
            memory == 0 or memory >= 8 * 1024 ** 3,
            human_bytes(memory) + " installed" if memory else "Could not be determined",
        )
    )

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
    if required_bytes:
        # Reference data unpacks alongside its download, so ask for headroom.
        needed = int(required_bytes * 1.15)
        checks.append(
            SystemCheck(
                "Disk space",
                available >= needed,
                f"{human_bytes(available)} free, about {human_bytes(needed)} needed",
                blocking=True,
            )
        )
    else:
        checks.append(
            SystemCheck("Disk space", True, f"{human_bytes(available)} free at {config.database_root}")
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
    return checks


def blocking_failures(checks: list[SystemCheck]) -> list[SystemCheck]:
    return [check for check in checks if check.blocking and not check.passed]
