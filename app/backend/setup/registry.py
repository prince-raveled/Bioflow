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
    #: A match for any of these globs inside the directory means "installed".
    marker_globs: tuple[str, ...]
    approximate_bytes: int


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
        approximate_bytes=33 * GIGABYTE,
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
        approximate_bytes=6 * GIGABYTE,
    ),
)


MICROMAMBA_APPROXIMATE_BYTES = 30 * MEGABYTE


def environment_specs() -> dict[str, EnvironmentSpec]:
    return {spec.key: spec for spec in ENVIRONMENTS}


def database_specs() -> dict[str, DatabaseSpec]:
    return {spec.key: spec for spec in DATABASES}
