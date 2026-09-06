"""Fetch BioFlow's private Micromamba runtime and large reference files.

These run in-process rather than as external commands so that a brand new
Linux machine needs nothing beyond Python: no curl, no tar, no system Conda.
"""

from collections.abc import Callable, Collection
from pathlib import Path
from fnmatch import fnmatch
import hashlib
import os
import platform
import tarfile
import tempfile
import urllib.error
import urllib.request

from backend.config import BioFlowConfig


MICROMAMBA_URL = "https://micro.mamba.pm/api/micromamba/{platform}/latest"
#: GENCODE release 43 primary assembly. The same file is offered over both
#: schemes from the same host: some networks fail the TLS handshake to this
#: host specifically while plain HTTP to it succeeds, which would otherwise
#: strand a user on a step host removal cannot proceed without. Integrity does
#: not rest on the transport - every download is checked against the release's
#: own published MD5 below.
GRCH38_DIRECTORY = "pub/databases/gencode/Gencode_human/release_43"
_GIGABYTE = 1024 * 1024 * 1024

GRCH38_FILENAME = "GRCh38.primary_assembly.genome.fa.gz"
GRCH38_SOURCES = (
    f"https://ftp.ebi.ac.uk/{GRCH38_DIRECTORY}/{GRCH38_FILENAME}",
    f"http://ftp.ebi.ac.uk/{GRCH38_DIRECTORY}/{GRCH38_FILENAME}",
)
#: The checksum is read from the release rather than pinned in code, so it can
#: never drift out of step with the file actually served.
GRCH38_CHECKSUM_SOURCES = tuple(
    f"{scheme}://ftp.ebi.ac.uk/{GRCH38_DIRECTORY}/MD5SUMS"
    for scheme in ("https", "http")
)

#: Kept for callers that only need the canonical location.
GRCH38_URL = GRCH38_SOURCES[0]

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

    # A connection cut mid-transfer ends the read loop indistinguishably from a
    # completed one, so a short file would otherwise be promoted to the final
    # name and treated as installed data from then on. When the server declared
    # a length, hold the partial file back instead: it stays a .part, and the
    # next attempt resumes from where this one stopped rather than restarting.
    if total and received < total:
        raise RuntimeError(
            f"The download of {destination.name} ended early: got "
            f"{human_bytes(received)} of {human_bytes(total)}. The partial file "
            f"has been kept, so running the install again resumes it."
        )

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


def published_md5(filename: str, log: LogCallback) -> str | None:
    """The release's own published MD5 for a file, or None when unavailable.

    Read from the server at download time rather than pinned in code: a stale
    constant would reject a file that is in fact correct.
    """
    for url in GRCH38_CHECKSUM_SOURCES:
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "BioFlow"})
            with urllib.request.urlopen(request, timeout=45) as response:
                listing = response.read().decode("utf-8", "replace")
        except Exception:  # noqa: BLE001 - any failure just means no checksum
            continue
        for line in listing.splitlines():
            parts = line.split()
            if len(parts) == 2 and parts[1].lstrip("*") == filename:
                return parts[0].lower()
    return None


def file_md5(path: Path) -> str:
    """MD5 of a file, read in chunks so a multi-gigabyte reference is fine."""
    digest = hashlib.md5()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def download_grch38(config: BioFlowConfig, log: LogCallback) -> None:
    """Fetch the GRCh38 primary assembly used to build the host-removal index."""
    # Always the managed store: an external reference never receives a download.
    reference = config.managed_grch38_directory / GRCH38_FILENAME
    if reference.is_file() and reference.stat().st_size > 700 * _MEGABYTE:
        log(f"GRCh38 reference already present: {reference}")
        return

    log("Downloading the GRCh38 human reference (about 806 MB).")
    failures = []
    for index, url in enumerate(GRCH38_SOURCES):
        try:
            download_file(url, reference, log)
            break
        except Exception as error:  # noqa: BLE001 - try the next source
            failures.append(f"{url}: {type(error).__name__}: {error}")
            if index + 1 < len(GRCH38_SOURCES):
                log(f"Source unavailable ({type(error).__name__}); trying the next one.")
    else:
        raise RuntimeError(
            "Could not download the GRCh38 reference from any source:\n  "
            + "\n  ".join(failures)
        )

    expected = published_md5(GRCH38_FILENAME, log)
    if expected is None:
        # The reference is still usable; say plainly that it was not verified.
        log("Could not read the published checksum, so the download was not verified.")
        return
    log("Verifying the download against the release's published checksum...")
    actual = file_md5(reference)
    if actual != expected:
        reference.unlink(missing_ok=True)
        raise RuntimeError(
            f"The downloaded reference is corrupt: expected MD5 {expected}, got {actual}. "
            f"The partial file has been removed; run the install again."
        )
    log(f"Checksum verified ({expected}).")


#: Extensions that only ever belong to a download, never to installed data.
#: Deliberately excludes a bare ".gz": several of these datasets are shipped as
#: directories of ".ffn.gz" sequence files that must survive cleanup.
#: Bare ".gz" and ".bz2" are deliberately absent. For several of these datasets
#: the compressed file *is* the database rather than a spent archive: HUMAnN
#: reads its ChocoPhlAn sequences straight out of *.ffn.gz. Only suffixes that
#: unambiguously denote a container are listed, so widening this is not the
#: safe-looking tidy-up it appears to be.
ARCHIVE_SUFFIXES = (".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tar.xz", ".zip", ".part")


def reclaim_archives(
    directory: Path, log: LogCallback, keep: Collection[str] = ()
) -> int:
    """Delete downloaded archives left behind after unpacking.

    Some tools remove their own archives and some do not, so this runs after
    every dataset install and reports what it actually freed. Returns the
    number of bytes reclaimed.
    """
    if not directory.is_dir():
        return 0
    freed = 0
    for path in sorted(directory.rglob("*")):
        if not path.is_file():
            continue
        # `keep` holds the dataset's required-file patterns, which may be globs,
        # so match them as such rather than by exact name.
        if any(fnmatch(path.name, pattern) for pattern in keep):
            continue
        if not any(path.name.endswith(suffix) for suffix in ARCHIVE_SUFFIXES):
            continue
        size = path.stat().st_size
        try:
            path.unlink()
        except OSError as error:  # noqa: PERF203 - report and keep going
            log(f"Could not remove {path.name}: {error}")
            continue
        freed += size
        log(f"Removed {path.name}, reclaiming {size / _GIGABYTE:.1f} GB.")
    if freed:
        log(f"Reclaimed {freed / _GIGABYTE:.1f} GB of archive files.")
    else:
        log("No leftover archives to remove; the tool cleaned up after itself.")
    return freed
