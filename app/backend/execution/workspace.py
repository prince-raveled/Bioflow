"""The on-disk layout of a BioFlow analysis, and the naming contract between stages.

Every stage reads the previous stage's outputs through this one module, so the
chain from raw reads to functional profiles has a single place that decides
where a file lives and what it is called.
"""

from dataclasses import dataclass
from pathlib import Path

from backend.release import functional_profiling_enabled
from backend.samples import FASTQ_EXTENSIONS, Sample


@dataclass(frozen=True)
class Workspace:
    """Directories and per-sample file names for one analysis."""

    root: Path

    # ---- directories -------------------------------------------------
    @property
    def qc_raw(self) -> Path:
        return self.root / "01_qc_raw"

    @property
    def trimmed(self) -> Path:
        return self.root / "02_trimmed"

    @property
    def qc_trimmed(self) -> Path:
        return self.root / "03_qc_trimmed"

    @property
    def host_removed(self) -> Path:
        return self.root / "04_host_removed"

    @property
    def taxonomy(self) -> Path:
        return self.root / "05_taxonomy"

    @property
    def functional(self) -> Path:
        return self.root / "06_functional"

    @property
    def multiqc(self) -> Path:
        return self.root / "07_multiqc"

    @property
    def logs(self) -> Path:
        return self.root / "logs"

    @property
    def checkpoint_file(self) -> Path:
        return self.root / "bioflow-run.json"

    @property
    def project_file(self) -> Path:
        return self.root / "bioflow-project.json"

    def all_directories(self) -> list[Path]:
        """Directories a run of this build will actually write into.

        Functional profiling is withheld from this release, and creating its
        directory anyway leaves an empty "06_functional" in every result folder,
        describing a stage that cannot run.
        """
        directories = [
            self.qc_raw,
            self.trimmed,
            self.qc_trimmed,
            self.host_removed,
            self.taxonomy,
            self.multiqc,
            self.logs,
        ]
        if functional_profiling_enabled():
            directories.insert(directories.index(self.multiqc), self.functional)
        return directories

    def create(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        for directory in self.all_directories():
            directory.mkdir(parents=True, exist_ok=True)

    # ---- per-sample file names ---------------------------------------
    def trimmed_reads(self, sample: Sample) -> list[Path]:
        """fastp output. Paired samples keep two separate mate files."""
        if sample.is_paired:
            return [
                self.trimmed / f"{sample.name}_R1.trim.fastq.gz",
                self.trimmed / f"{sample.name}_R2.trim.fastq.gz",
            ]
        return [self.trimmed / f"{sample.name}.trim.fastq.gz"]

    def fastp_report(self, sample: Sample, extension: str) -> Path:
        return self.trimmed / f"{sample.name}_fastp.{extension}"

    def host_removed_reads(self, sample: Sample) -> list[Path]:
        """Bowtie2 unmapped output. Pairing is preserved for paired samples."""
        if sample.is_paired:
            return [
                self.host_removed / f"{sample.name}_nohost_R1.fastq.gz",
                self.host_removed / f"{sample.name}_nohost_R2.fastq.gz",
            ]
        return [self.host_removed / f"{sample.name}_nohost.fastq.gz"]

    def host_removed_pattern(self, sample: Sample) -> Path:
        """Bowtie2's --un-conc-gz template, where %  becomes the mate number."""
        return self.host_removed / f"{sample.name}_nohost_R%.fastq.gz"

    def bowtie2_log(self, sample: Sample) -> Path:
        return self.host_removed / f"{sample.name}_bowtie2.log"

    def taxonomic_profile(self, sample: Sample) -> Path:
        return self.taxonomy / f"{sample.name}_profile.txt"

    def taxonomic_map(self, sample: Sample) -> Path:
        """MetaPhlAn's read-to-marker mapping, saved by --mapout.

        Not a BAM, despite what this file used to be called. MetaPhlAn writes
        this as text and compresses it only when the name ends in ".bz2"
        (metaphlan.py: `bz2.open(...) if self.mapout.endswith(".bz2") else
        open(...)`), so the old name both misdescribed the format and opted out
        of the compression the tool expects to apply to it.
        """
        return self.taxonomy / f"{sample.name}_map.txt.bz2"

    def functional_directory(self, sample: Sample) -> Path:
        return self.functional / sample.name

    def humann_input(self, sample: Sample) -> Path:
        """HUMAnN accepts one file, so paired mates are concatenated into this."""
        if sample.is_paired:
            return self.functional_directory(sample) / f"{sample.name}_combined.fastq.gz"
        return self.host_removed_reads(sample)[0]

    def humann_outputs(self, sample: Sample) -> dict[str, Path]:
        directory = self.functional_directory(sample)
        stem = self.humann_input(sample).name
        for extension in FASTQ_EXTENSIONS:
            if stem.lower().endswith(extension):
                stem = stem[: -len(extension)]
                break
        return {
            "genefamilies": directory / f"{stem}_genefamilies.tsv",
            "pathabundance": directory / f"{stem}_pathabundance.tsv",
            "pathcoverage": directory / f"{stem}_pathcoverage.tsv",
        }

    def humann_normalised(self, sample: Sample, kind: str) -> Path:
        return self.functional_directory(sample) / f"{sample.name}_{kind}_cpm.tsv"

    def multiqc_report(self) -> Path:
        return self.multiqc / "multiqc_report.html"

    def stage_log(self, stage_key: str, sample_name: str, stream: str) -> Path:
        return self.logs / f"{stage_key}__{sample_name}.{stream}.log"
