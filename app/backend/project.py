"""A BioFlow analysis project: its samples, options, and on-disk workspace."""

from dataclasses import dataclass, field
from pathlib import Path
import json

from backend.config import METAPHLAN_INDEX, BioFlowConfig, get_config
from backend.execution.stage import RunContext, RunOptions
from backend.execution.workspace import Workspace
from backend.samples import ReadLayout, Sample, detect_samples, validate_sample


PROJECT_FORMAT_VERSION = 2


@dataclass
class Project:
    """Everything needed to reproduce one analysis."""

    name: str
    root: Path
    samples: list[Sample] = field(default_factory=list)
    #: The layout the user confirmed; detection only proposes it.
    layout: ReadLayout = ReadLayout.SINGLE
    options: RunOptions = field(default_factory=RunOptions)
    #: Stage keys selected for this project, in workflow order.
    stage_keys: list[str] = field(default_factory=list)

    # ------------------------------------------------------------------
    @property
    def workspace(self) -> Workspace:
        return Workspace(self.root)

    def context(self, config: BioFlowConfig | None = None) -> RunContext:
        """Bind the project to this machine's installed references."""
        resolved = config or get_config()
        return RunContext(
            workspace=self.workspace,
            options=self.options,
            host_index_prefix=resolved.grch38_index_prefix,
            metaphlan_database=resolved.metaphlan_database_directory,
            metaphlan_index=METAPHLAN_INDEX,
        )

    # ------------------------------------------------------------------
    @classmethod
    def from_files(
        cls,
        name: str,
        root: Path,
        files: list[Path],
        layout: ReadLayout | None = None,
        options: RunOptions | None = None,
    ) -> tuple["Project", list[str]]:
        """Build a project from selected FASTQ files.

        The layout may be left to detection or forced by the user; either way the
        returned problems list says exactly why any file was not used.
        """
        detection = detect_samples(files, layout)
        resolved_layout = layout or (
            detection.samples[0].layout if detection.samples else ReadLayout.SINGLE
        )
        project = cls(
            name=name,
            root=Path(root),
            samples=detection.samples,
            layout=resolved_layout,
            options=options or RunOptions(),
        )
        return project, list(detection.problems)

    def validate_inputs(self) -> list[str]:
        """Check every sample's files before committing to a long analysis."""
        problems: list[str] = []
        if not self.samples:
            problems.append("This project has no samples.")
        for sample in self.samples:
            problems.extend(validate_sample(sample))
        return problems

    # ------------------------------------------------------------------
    def save(self) -> Path:
        """Write bioflow-project.json so the project is portable and re-openable."""
        self.workspace.create()
        payload = {
            "format_version": PROJECT_FORMAT_VERSION,
            "name": self.name,
            "layout": self.layout.value,
            "stage_keys": self.stage_keys,
            "options": {
                "threads": self.options.threads,
                "humann_protein_only": self.options.humann_protein_only,
                "humann_normalise": self.options.humann_normalise,
            },
            "samples": [
                {
                    "name": sample.name,
                    "layout": sample.layout.value,
                    "read1": str(sample.read1),
                    "read2": str(sample.read2) if sample.read2 else None,
                }
                for sample in self.samples
            ],
        }
        path = self.workspace.project_file
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return path

    @classmethod
    def load(cls, root: Path) -> "Project | None":
        path = Workspace(Path(root)).project_file
        if not path.is_file():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

        samples: list[Sample] = []
        for entry in payload.get("samples", []):
            try:
                samples.append(
                    Sample(
                        name=entry["name"],
                        layout=ReadLayout(entry["layout"]),
                        read1=Path(entry["read1"]),
                        read2=Path(entry["read2"]) if entry.get("read2") else None,
                    )
                )
            except (KeyError, ValueError):
                continue

        stored = payload.get("options", {})
        return cls(
            name=payload.get("name", Path(root).name),
            root=Path(root),
            samples=samples,
            layout=ReadLayout(payload.get("layout", "single")),
            options=RunOptions(
                threads=int(stored.get("threads", 4)),
                humann_protein_only=bool(stored.get("humann_protein_only", True)),
                humann_normalise=bool(stored.get("humann_normalise", True)),
            ),
            stage_keys=list(payload.get("stage_keys", [])),
        )
