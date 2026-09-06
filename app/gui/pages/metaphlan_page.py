"""Taxonomic profiling on its own, for files the user picks directly.

The workflow page runs MetaPhlAn on host-removed reads as one step of a chain.
This page runs it on whatever FASTQ is chosen, which is what you want when the
reads have already been prepared elsewhere, or when only a profile is needed.

How MetaPhlAn is invoked is not decided here. The command comes from the same
stage the workflow uses, so the two cannot drift into running the profiler
differently - including the memory-mapped Bowtie2 the 33 GB index needs.
"""

from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QPushButton, QSlider

from backend.config import METAPHLAN_INDEX, get_config
from backend.execution.stage import RunContext, RunOptions
from backend.execution.stages.taxonomy import MetaPhlAnStage
from backend.execution.workspace import Workspace
from backend.samples import (
    FASTQ_FILE_FILTER,
    ReadLayout,
    Sample,
    sample_name_for,
    strip_fastq_extension,
)
from backend.setup.manager import SetupManager
from backend.setup.registry import database_specs
from gui import dialogs
from gui.pages.qc_tool_page import QCToolPage


#: Shared with the workflow page's control of the same name.
SUBSAMPLE_CHOICES = (
    ("All reads", None),
    ("100k pairs", 100_000),
    ("500k pairs", 500_000),
    ("1M pairs", 1_000_000),
)


class MetaPhlAnPage(QCToolPage):
    """Profile microbial composition from one sample's reads."""

    def __init__(self):
        super().__init__(
            "MetaPhlAn", environment_key="taxonomy", section="Taxonomic profiling"
        )
        self.fastq_files: list[str] = []
        self.description.setText(
            "Estimate which microbes are present, and in what proportion, from "
            "clade-specific marker genes. Reads should already have had host "
            "sequence removed, and must be at least 70 bp."
        )

        self.database_label = QLabel("")
        self.database_label.setObjectName("componentDescription")
        self.database_label.setWordWrap(True)
        self.controls.addWidget(self.database_label)

        self.file_label = QLabel("No FASTQ files selected")
        self.file_label.setWordWrap(True)
        self.controls.addWidget(self.file_label)

        chooser = QHBoxLayout()
        browse = QPushButton("Select FASTQ files")
        browse.clicked.connect(self.select_files)
        chooser.addWidget(browse)
        chooser.addWidget(QLabel("Read layout"))
        self.layout_choice = QComboBox()
        for layout in (ReadLayout.SINGLE, ReadLayout.PAIRED):
            self.layout_choice.addItem(layout.label, layout)
        self.layout_choice.currentIndexChanged.connect(lambda _i: self._describe_selection())
        chooser.addWidget(self.layout_choice)
        chooser.addStretch()
        self.controls.addLayout(chooser)

        options = QHBoxLayout()
        options.addWidget(QLabel("Profile"))
        self.subsample_choice = QComboBox()
        for label, value in SUBSAMPLE_CHOICES:
            self.subsample_choice.addItem(label, value)
        self.subsample_choice.setToolTip(
            "How much of the sample to profile. Every read is used unless a "
            "limit is chosen; a limit finishes sooner at the cost of the reads "
            "it leaves out."
        )
        options.addWidget(self.subsample_choice)

        options.addWidget(QLabel("Threads"))
        self.threads = QSlider(Qt.Orientation.Horizontal)
        self.threads.setRange(1, 32)
        self.threads.setValue(self.default_thread_count())
        self.threads.valueChanged.connect(
            lambda value: self.thread_count.setText(self.thread_label(value))
        )
        options.addWidget(self.threads)
        self.thread_count = QLabel(self.thread_label(self.threads.value()))
        self.thread_count.setObjectName("threadCount")
        options.addWidget(self.thread_count)
        self.controls.addLayout(options)

        self.add_output_selector()
        self.refresh_database_state()

    # ------------------------------------------------------------------
    def chosen_layout(self) -> ReadLayout:
        return self.layout_choice.currentData()

    def refresh_database_state(self) -> None:
        """Say whether the marker database is usable, before anything is run."""
        config = get_config()
        manager = SetupManager(config)
        specification = database_specs()["metaphlan_chocophlan"]
        state = manager.database_state(specification).state
        if state.usable:
            self.database_label.setText(
                f"Marker database: {state.label} — {config.metaphlan_database_directory}"
            )
            return
        missing = manager.missing_database_files(specification)
        detail = f" Missing {len(missing)} required file(s)." if missing else ""
        self.database_label.setText(
            f"Marker database: {state.label}.{detail} "
            f"Install it from Setup & Resources before profiling."
        )

    def select_files(self) -> None:
        files = dialogs.open_files(self, "Select FASTQ files", FASTQ_FILE_FILTER)
        if not files:
            return
        self.fastq_files = files
        self.set_default_output_directory(
            Path(files[0]).resolve().parent / "bioflow_results" / "metaphlan"
        )
        self._describe_selection()
        self.add_log(f"Selected {len(files)} FASTQ file(s).")

    def _describe_selection(self) -> None:
        self.file_label.setText("\n".join(self.fastq_files) or "No FASTQ files selected")
        count = len(self.fastq_files)
        layout = self.chosen_layout()
        if layout is ReadLayout.PAIRED and count != 2:
            self.file_label.setText(
                self.file_label.text()
                + f"\n\nPaired-end profiling takes exactly two files; {count} selected."
            )
        self.set_input_summary(f"{count} FASTQ file(s) staged for MetaPhlAn")

    # ------------------------------------------------------------------
    def _sample(self) -> Sample:
        """The reads as the profiler models them."""
        paths = [Path(name) for name in self.fastq_files]
        # The shared rule, not Path.stem: a stem only removes ".gz", leaving
        # names like "sample_nohost_R1.fastq" to be carried into every output
        # file. Paired input also drops the mate marker, so both mates describe
        # one sample rather than naming the results after read one.
        name = sample_name_for(paths[0]) if len(paths) > 1 else \
            strip_fastq_extension(paths[0].name).strip("._-")
        if self.chosen_layout() is ReadLayout.PAIRED:
            return Sample(name, ReadLayout.PAIRED, paths[0], paths[1])
        return Sample(name, ReadLayout.SINGLE, paths[0])

    def run_analysis(self) -> None:
        layout = self.chosen_layout()
        expected = 2 if layout is ReadLayout.PAIRED else 1
        if len(self.fastq_files) != expected:
            self.add_log(
                f"{layout.label} profiling takes exactly {expected} file(s); "
                f"{len(self.fastq_files)} selected."
            )
            return
        if self.output_directory is None:
            self.add_log("Choose an output folder before profiling.")
            return

        config = get_config()
        manager = SetupManager(config)
        specification = database_specs()["metaphlan_chocophlan"]
        if not manager.database_installed(specification):
            self.add_log(
                "The MetaPhlAn marker database is not installed. "
                "Install it from Setup & Resources first."
            )
            self.refresh_database_state()
            return

        self.output_directory.mkdir(parents=True, exist_ok=True)
        sample = self._sample()
        stage = MetaPhlAnStage()
        # A workspace rooted at the chosen folder, so the profiler writes where
        # the stage expects to and this page does not invent its own layout.
        context = RunContext(
            workspace=Workspace(self.output_directory),
            options=RunOptions(
                threads=self.threads.value(),
                metaphlan_subsample_pairs=self.subsample_choice.currentData(),
            ),
            metaphlan_database=config.metaphlan_database_directory,
            metaphlan_index=METAPHLAN_INDEX,
            bowtie2_memory_mapped_shim=config.bowtie2_memory_mapped_shim,
            micromamba_binary=config.micromamba_binary,
            micromamba_root=config.micromamba_root,
            taxonomy_environment=config.environment_name("taxonomy"),
        )
        # Only what this page writes into. Creating the whole pipeline layout
        # left a user who asked for one profile with seven empty numbered
        # directories in their chosen folder, describing stages that never ran.
        context.workspace.taxonomy.mkdir(parents=True, exist_ok=True)
        # Puts the memory-mapped Bowtie2 shim in place; without it the aligner
        # loads the 33 GB index into anonymous memory and is killed.
        stage.prepare(sample, context, self.add_log)

        command = stage.profile_command(
            reads=sample.reads(),
            profile=context.workspace.taxonomic_profile(sample),
            mapout=context.workspace.taxonomic_map(sample),
            context=context,
            paired=sample.is_paired,
            label=sample.name,
        )
        self.add_log(
            "Profiling with a memory-mapped index. The first minutes are spent "
            "loading it, with no output; that is normal."
        )
        self.start_tool(list(command.command))
