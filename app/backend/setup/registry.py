"""Declarative description of every backend BioFlow knows how to install.

Adding a tool to BioFlow starts here: describe its environment and reference
data once, and the setup manager, the Setup page, and the disk-space preflight
all pick it up without further changes.
"""

from dataclasses import dataclass

from backend.config import METAPHLAN_INDEX


GIGABYTE = 1024 ** 3
MEGABYTE = 1024 ** 2


@dataclass(frozen=True)
class EnvironmentSpec:
    """One Micromamba environment and the tools it must provide."""

    key: str
    title: str
    description: str
    channels: tuple[str, ...]
    packages: tuple[str, ...]
    #: Commands run inside the finished environment to prove it works.
    verify_commands: tuple[tuple[str, ...], ...]
    approximate_bytes: int


@dataclass(frozen=True)
class DatabaseSpec:
    """One reference dataset, and how to recognise a complete installation."""

    key: str
    title: str
    description: str
    #: Environment whose tools perform the download and verification.
    environment_key: str
    #: Location relative to the configured database root.
    directory_parts: tuple[str, ...]
    #: A match for any of these globs inside the directory means "present".
    marker_globs: tuple[str, ...]
    #: Space the installed dataset occupies once unpacked.
    approximate_bytes: int
    #: Every one of these must match before the dataset counts as complete.
    #: A single marker file is not enough: an install interrupted part-way
    #: through unpacking can leave the marker behind without the bulk of the
    #: data, and would otherwise be reported as ready to use.
    required_globs: tuple[str, ...] = ()
    #: Bytes actually pulled over the network. Several of these datasets ship as
    #: a compressed bundle that unpacks to many times its transfer size, so the
    #: two figures must not be conflated: one sets the user's expectation of how
    #: long a download takes, the other gates disk space.
    download_bytes: int = 0
    #: Peak space needed mid-install, when the archive and its unpacked contents
    #: both exist. Defaults to download + installed, which is the usual shape.
    peak_bytes: int = 0
    #: Resident memory needed to *use* this dataset, which is a different
    #: question from installing it: a machine can have ample disk and still be
    #: unable to hold the index the aligner memory-maps into RAM.
    runtime_memory_bytes: int = 0

    def transfer_size(self) -> int:
        return self.download_bytes or self.approximate_bytes

    def peak_disk(self) -> int:
        if self.peak_bytes:
            return self.peak_bytes
        return self.approximate_bytes + self.transfer_size()


ENVIRONMENTS: tuple[EnvironmentSpec, ...] = (
    EnvironmentSpec(
        key="qc",
        title="Quality control",
        description="FastQC, fastp, and MultiQC for read quality and trimming.",
        channels=("conda-forge", "bioconda"),
        packages=("fastqc", "fastp", "multiqc"),
        verify_commands=(
            ("fastqc", "--version"),
            ("fastp", "--version"),
            ("multiqc", "--version"),
        ),
        approximate_bytes=2 * GIGABYTE,
    ),
    EnvironmentSpec(
        key="hostrem",
        title="Host removal",
        description="Bowtie2, samtools, and seqkit for removing human reads.",
        channels=("conda-forge", "bioconda"),
        packages=("bowtie2", "samtools", "bwa", "fastp", "pigz", "seqkit"),
        verify_commands=(
            ("bowtie2", "--version"),
            ("samtools", "--version"),
            ("seqkit", "version"),
        ),
        approximate_bytes=1 * GIGABYTE,
    ),
    EnvironmentSpec(
        key="taxonomy",
        title="Taxonomic profiling",
        description="MetaPhlAn 4 with Bowtie2 for marker-gene taxonomic profiles.",
        channels=("conda-forge", "bioconda"),
        packages=("metaphlan", "bowtie2"),
        verify_commands=(
            ("metaphlan", "--version"),
            ("bowtie2", "--version"),
        ),
        approximate_bytes=4 * GIGABYTE,
    ),
    EnvironmentSpec(
        key="function",
        title="Functional profiling",
        description="HUMAnN 3 with DIAMOND for gene-family and pathway profiles.",
        channels=("biobakery", "bioconda", "conda-forge"),
        packages=(
            "python=3.9",
            "humann",
            "metaphlan",
            "diamond",
            "bowtie2",
            "samtools",
            "pigz",
        ),
        verify_commands=(
            ("humann", "--version"),
            ("diamond", "--version"),
        ),
        approximate_bytes=3 * GIGABYTE,
    ),
)


DATABASES: tuple[DatabaseSpec, ...] = (
    DatabaseSpec(
        key="grch38",
        title="Human reference (GRCh38 + Bowtie2 index)",
        description=(
            "GENCODE GRCh38 primary assembly, indexed for Bowtie2. Required "
            "before host reads can be removed."
        ),
        environment_key="hostrem",
        directory_parts=("human", "hg38"),
        marker_globs=("GRCh38_index.1.bt2", "GRCh38_index.1.bt2l"),
        approximate_bytes=6 * GIGABYTE,
        # Only the compressed assembly crosses the network; the bulk of the
        # footprint is the Bowtie2 index, which is built locally afterwards.
        download_bytes=850 * MEGABYTE,
        peak_bytes=7 * GIGABYTE,
    ),
    DatabaseSpec(
        key="metaphlan_chocophlan",
        title=f"MetaPhlAn markers ({METAPHLAN_INDEX})",
        description=(
            "Clade-specific marker genes and Bowtie2 indexes. Large, but it "
            "lets MetaPhlAn run fully offline afterwards."
        ),
        environment_key="taxonomy",
        directory_parts=("metaphlan",),
        marker_globs=(f"{METAPHLAN_INDEX}.pkl", "*.pkl"),
        # The marker table plus the six Bowtie2 index files MetaPhlAn needs to
        # profile offline; without all of these a run fails at alignment time.
        required_globs=(
            f"{METAPHLAN_INDEX}.pkl",
            f"{METAPHLAN_INDEX}.1.bt2l",
            f"{METAPHLAN_INDEX}.2.bt2l",
            f"{METAPHLAN_INDEX}.3.bt2l",
            f"{METAPHLAN_INDEX}.4.bt2l",
            f"{METAPHLAN_INDEX}.rev.1.bt2l",
            f"{METAPHLAN_INDEX}.rev.2.bt2l",
        ),
        # Measured on a completed install: 50.5 GB on disk once the marker
        # bundle's .fna.bz2 files are decompressed in place.
        approximate_bytes=51 * GIGABYTE,
        # Measured from an actual install: `metaphlan --install` fetches the
        # prebuilt Bowtie2 index tar (33,433 MB) and then the marker bundle
        # (4,886 MB), unpacking each in place and deleting the archive as it
        # goes. That staging keeps the peak well below download + installed;
        # 68,069 MB was the high-water mark sampled through a full install.
        download_bytes=38 * GIGABYTE,
        peak_bytes=68 * GIGABYTE,
        # Measured from an OOM kill: bowtie2-align-l held 9,786,628 kB of
        # anonymous memory aligning against the ChocoPhlAn index.
        runtime_memory_bytes=10 * GIGABYTE,
    ),
    DatabaseSpec(
        key="humann_chocophlan",
        title="HUMAnN ChocoPhlAn (nucleotide)",
        description=(
            "Species pangenomes for HUMAnN's nucleotide search. Only needed "
            "for full-resolution runs, not for the default protein-only mode."
        ),
        environment_key="function",
        directory_parts=("humann", "chocophlan"),
        marker_globs=("*.ffn.gz", "*.ffn"),
        required_globs=("*.ffn.gz",),
        approximate_bytes=17 * GIGABYTE,
    ),
    DatabaseSpec(
        key="humann_uniref50",
        title="HUMAnN UniRef50 (DIAMOND)",
        description=(
            "Translated protein database for HUMAnN's DIAMOND search. This is "
            "the one HUMAnN needs in the default protein-only mode."
        ),
        environment_key="function",
        directory_parts=("humann", "uniref"),
        marker_globs=("*.dmnd", "uniref/*.dmnd"),
        required_globs=("*.dmnd",),
        approximate_bytes=6 * GIGABYTE,
    ),
)


MICROMAMBA_APPROXIMATE_BYTES = 30 * MEGABYTE


def environment_specs() -> dict[str, EnvironmentSpec]:
    return {spec.key: spec for spec in ENVIRONMENTS}


def database_specs() -> dict[str, DatabaseSpec]:
    return {spec.key: spec for spec in DATABASES}
