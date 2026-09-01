"""Fetch BioFlow's private Micromamba runtime and large reference files.

These run in-process rather than as external commands so that a brand new
Linux machine needs nothing beyond Python: no curl, no tar, no system Conda.
"""

from collections.abc import Callable
from pathlib import Path
import os
import platform
import tarfile
import tempfile
import urllib.error
import urllib.request

from backend.config import BioFlowConfig


MICROMAMBA_URL = "https://micro.mamba.pm/api/micromamba/{platform}/latest"
GRCH38_URL = (
    "https://ftp.ebi.ac.uk/pub/databases/gencode/Gencode_human/release_43/"
    "GRCh38.primary_assembly.genome.fa.gz"
)

_MEGABYTE = 1024 * 1024
_CHUNK = 1024 * 256

LogCallback = Callable[[str], None]


def micromamba_platform() -> str:
    """Map this machine's architecture onto a Micromamba release name."""
    machine = platform.machine().lower()
    if machine in ("x86_64", "amd64"):
        return "linux-64"
    if machine in ("aarch64", "arm64"):
        return "linux-aarch64"
    if machine in ("ppc64le",):
        return "linux-ppc64le"
    raise RuntimeError(f"Unsupported Linux architecture: {platform.machine()}")


def human_bytes(size: int) -> str:
    """Format a byte count for status lines and disk-space warnings."""
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.0f} {unit}" if unit in ("B", "KB") else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TB"


def download_file(url: str, destination: Path, log: LogCallback) -> None:
    """Download a file, resuming a partial download when the server allows it."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(destination.name + ".part")
    existing = partial.stat().st_size if partial.is_file() else 0

    request = urllib.request.Request(url, headers={"User-Agent": "BioFlow"})
    if existing:
        request.add_header("Range", f"bytes={existing}-")
        log(f"Resuming download at {human_bytes(existing)}: {url}")
    else:
        log(f"Downloading {url}")

    try:
        response = urllib.request.urlopen(request, timeout=60)
    except urllib.error.HTTPError as error:
        if existing and error.code == 416:
            # The partial file is already complete.
            partial.replace(destination)
            log(f"Download already complete: {destination.name}")
            return
        raise

    with response:
        resuming = response.status == 206
        if existing and not resuming:
            log("Server does not support resuming; restarting the download.")
            existing = 0
        mode = "ab" if resuming and existing else "wb"
        declared = response.headers.get("Content-Length")
        total = (int(declared) + existing) if declared else 0

        received = existing
        next_report = received + 50 * _MEGABYTE
        with partial.open(mode) as handle:
            while chunk := response.read(_CHUNK):
                handle.write(chunk)
                received += len(chunk)
                if received >= next_report:
                    if total:
                        log(
                            f"  {human_bytes(received)} of {human_bytes(total)} "
                            f"({received * 100 // total}%)"
                        )
                    else:
                        log(f"  {human_bytes(received)} downloaded")
                    next_report = received + 50 * _MEGABYTE

    partial.replace(destination)
    log(f"Downloaded {destination.name} ({human_bytes(destination.stat().st_size)})")


def install_micromamba(config: BioFlowConfig, log: LogCallback) -> None:
    """Install BioFlow's private Micromamba binary into its data directory."""
    binary = config.micromamba_binary
    if binary.is_file() and os.access(binary, os.X_OK):
        log(f"Micromamba is already installed: {binary}")
        return

    url = MICROMAMBA_URL.format(platform=micromamba_platform())
    binary.parent.mkdir(parents=True, exist_ok=True)
    config.micromamba_root.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="bioflow-micromamba-") as temporary:
        archive = Path(temporary) / "micromamba.tar.bz2"
        download_file(url, archive, log)
        log("Extracting the Micromamba runtime...")
        with tarfile.open(archive, "r:bz2") as bundle:
            member = bundle.getmember("bin/micromamba")
            extracted = bundle.extractfile(member)
            if extracted is None:
                raise RuntimeError("The Micromamba archive did not contain bin/micromamba.")
            with extracted, binary.open("wb") as handle:
                while chunk := extracted.read(_CHUNK):
                    handle.write(chunk)
    binary.chmod(0o755)
    log(f"Micromamba installed: {binary}")


def download_grch38(config: BioFlowConfig, log: LogCallback) -> None:
    """Fetch the GRCh38 primary assembly used to build the host-removal index."""
    # Always the managed store: an external reference never receives a download.
    reference = config.managed_grch38_directory / "GRCh38.primary_assembly.genome.fa.gz"
    if reference.is_file() and reference.stat().st_size > 700 * _MEGABYTE:
        log(f"GRCh38 reference already present: {reference}")
        return
    log("Downloading the GRCh38 human reference (about 806 MB).")
    download_file(GRCH38_URL, reference, log)
