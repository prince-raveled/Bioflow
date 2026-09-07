"""Taxonomic profiling with MetaPhlAn 4."""

from pathlib import Path
import os
import stat as stat_module

from backend.execution.record import ValidationResult
from backend.resources import (
    available_memory_bytes as detect_available_memory,
    memory_limit_bytes,
)
from backend.execution.stage import (
    LogCallback,
    RunContext,
    Stage,
    StageCommand,
    check_exists,
    count_data_rows,
)
from backend.samples import Sample


#: Ceiling on the threads handed to MetaPhlAn, whatever the run is configured
#: with.
#:
#: One, not more, and the reason is not memory. Bowtie2 held 9.0-9.6 GB of
#: anonymous memory against the 33 GB ChocoPhlAn index on a 14 GB machine and
#: was killed by the kernel; memory-mapping the index fixed that, and a
#: single-threaded run then completed in 1h46m.
#:
#: Raising this to two did not double throughput, it collapsed it. Measured over
#: 6h37m of a two-thread run that never finished: 21,851,582 major page faults
#: and 35.3 TB read from disk, the whole index fetched back 1,096 times over,
#: with kswapd0 burning 2h30m of CPU. A memory-mapped index only performs while
#: its hot pages stay in page cache, and here 33 GB of index competes for about
#: 5 GB of cache. A second thread does not add memory, it adds a second
#: independent access pattern evicting the first thread's pages. On this class
#: of machine the aligner is bound by page faults, not by CPU, and threads make
#: that worse rather than better.
#:
#: Deliberately a constant rather than something derived from free memory at run
#: time: the command is part of the checkpoint fingerprint, so a value that
#: moved between runs would invalidate results that are still perfectly good.
MAXIMUM_THREADS = 1

#: Total RAM at or above which Bowtie2 should load its index normally instead
#: of memory-mapping it.
#:
#: Memory-mapping is a rescue, not an optimisation. It is what makes the 33 GB
#: index usable on a machine that cannot hold it - and it costs enormously when
#: applied to a machine that could. Measured here at 14 GB: 37 reads a second,
#: because nearly every index probe faults in from disk. Bowtie2 against an
#: in-memory index runs four orders of magnitude faster than that.
#:
#: The figure comes from what a normal load actually needs: Bowtie2 was measured
#: holding 9.0-9.6 GB for this index, MetaPhlAn's marker table adds about 6.8 GB,
#: and an operating system with a desktop on it wants several more. Twenty-four
#: gigabytes is where that fits without crowding.
#:
#: Deliberately total RAM rather than free memory: this decision is part of the
#: command, and therefore part of the checkpoint fingerprint. Installed memory
#: does not change between two runs on the same machine; free memory does.
MEMORY_MAPPING_THRESHOLD_BYTES = 24 * 1024 ** 3


def should_memory_map(total_memory: int | None = None) -> bool:
    """Whether this machine has to memory-map the index rather than load it."""
    if total_memory is None:
        total_memory = total_memory_bytes()
    if not total_memory:
        # Unknown: assume the cautious path. Being slow beats being killed.
        return True
    return total_memory < MEMORY_MAPPING_THRESHOLD_BYTES


def total_memory_bytes() -> int:
    """Memory this run may use, or 0 when it cannot be determined.

    Installed RAM on an ordinary desktop, which is what this always used to
    report. Where a cgroup caps the process - what a container does - that cap
    wins, because `/proc/meminfo` inside a container describes the host and not
    the confinement: believing it would choose to load a 33 GB index into a
    cgroup that kills for trying.
    """
    return memory_limit_bytes()


#: What MetaPhlAn's own process needs before a single read is aligned. The
#: marker table ships as a 164 MB pickle and is loaded whole - measured at
#: 5.8-6.7 GB of anonymous memory across several runs - and there is no option
#: to page it. Bowtie2's index is on top of this, though memory-mapping keeps
#: most of that reclaimable.
MARKER_TABLE_BYTES = 7 * 1024 ** 3


def available_memory_bytes() -> int:
    """Memory free for a new process, or 0 when it cannot be read.

    Delegated, so this and preflight cannot disagree: they answered the same
    question with two separate implementations, and neither saw a cgroup.
    """
    return detect_available_memory()


def out_of_memory_penalty() -> int:
    """How much this process has been biased towards being killed, 0-1000.

    Anything above zero means the kernel will prefer to kill this process, and
    its children, ahead of others using the same memory. Editors and desktop
    session managers commonly set a positive value on everything they launch:
    VS Code uses 100. An analysis started from such a terminal is then chosen
    as the victim even when the machine has memory to spare, and the same
    command run from an ordinary shell succeeds. The value can only ever be
    raised by an unprivileged process, so this cannot be corrected from here -
    only reported.
    """
    try:
        return int(Path("/proc/self/oom_score_adj").read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return 0


#: Input size, compressed, past which profiling every read stops being a
#: sensible default on a machine where the index has to be memory-mapped.
#:
#: Measured on the 14 GB machine this was developed against: ~200,000 reads,
#: 16 MB of gzipped input, took 88 minutes of alignment. That is Bowtie2 moving
#: at about 38 reads a second, which is not the aligner being slow - it is the
#: 33 GB index being re-read from disk because it cannot stay in a ~5 GB page
#: cache. The rate is a property of the machine, not of BioFlow, and it is far
#: higher where the index fits in memory. The threshold is therefore a prompt
#: to think, not a prediction: at roughly 30x the measured input, a full-depth
#: sample would run for days rather than hours.
LARGE_INPUT_BYTES = 500 * 1024 ** 2

#: The reference point above, so the warning can say how much larger this is.
_MEASURED_INPUT_BYTES = 16 * 1024 ** 2
_MEASURED_MINUTES = 88


def profiling_scale_warning(reads: list[Path], subsample: int | None) -> str:
    """Say when profiling every read of a large sample is likely a mistake.

    Only when no limit has been set: choosing a pair count already answers this
    question, and the run then takes the same time whatever the input size.
    """
    if subsample:
        return ""
    try:
        total = sum(path.stat().st_size for path in reads if path.is_file())
    except OSError:
        return ""
    if total < LARGE_INPUT_BYTES:
        return ""
    ratio = total / _MEASURED_INPUT_BYTES
    hours = (_MEASURED_MINUTES * ratio) / 60
    return (
        f"This sample is {total / 1024 ** 2:.0f} MB compressed, about {ratio:.0f} "
        f"times the input that took {_MEASURED_MINUTES} minutes to profile here. "
        f"Profiling every read could take on the order of {hours:.0f} hours on a "
        f"machine where the marker index does not fit in memory. Choosing a pair "
        f"count instead makes the run take the same time whatever the input size."
    )


def memory_warnings() -> list[str]:
    """Reasons this machine may not get through a profiling run, if any."""
    warnings: list[str] = []
    available = available_memory_bytes()
    if available and available < MARKER_TABLE_BYTES:
        warnings.append(
            f"Only {available / 1024 ** 3:.1f} GB of memory is free and MetaPhlAn "
            f"needs about {MARKER_TABLE_BYTES / 1024 ** 3:.0f} GB to load its marker "
            f"table. Close other applications before running."
        )
    penalty = out_of_memory_penalty()
    if penalty > 0:
        warnings.append(
            f"This process is marked as a preferred target for the kernel's "
            f"out-of-memory killer (oom_score_adj={penalty}), which is what an "
            f"editor's built-in terminal does to everything it starts. "
            f"Profiling can be killed here even with memory to spare. Start "
            f"BioFlow from an ordinary terminal or its desktop entry instead."
        )
    return warnings


#: Written by BioFlow, never by hand. MetaPhlAn accepts a path to the Bowtie2
#: executable but offers no way to pass arguments through to it, so the option
#: that makes this fit in memory is added by pointing MetaPhlAn at this instead.
SHIM_TEMPLATE = """#!/usr/bin/env bash
# Written by BioFlow. Runs Bowtie2 with its index memory-mapped.
#
# MetaPhlAn's Bowtie2 index is 33 GB. Loaded the ordinary way it is read into
# anonymous memory, which on a machine with 14 GB of RAM ends with the kernel
# killing the aligner part-way through a run. With --mm the index is mapped
# from disk instead: the pages are shared, reclaimable under pressure, and the
# same alignment fits in well under half the memory.
#
# Launched through Micromamba rather than by running the executable directly:
# Bowtie2 is a Perl script that needs its environment activated, and calling it
# straight out of the environment prefix fails on a missing Sys::Hostname. Going
# through Micromamba also means the shim works whether or not whatever called it
# was already inside the environment.
exec {micromamba} run -r {root} -n {environment} bowtie2 --mm "$@"
"""


def write_memory_mapped_shim(
    shim: Path, micromamba: Path, root: Path, environment: str
) -> Path:
    """Create or refresh the Bowtie2 shim, and return where it is.

    Rewritten whenever it is out of date rather than only when absent, so an
    environment that has been reinstalled somewhere else cannot leave the shim
    pointing at an executable that is no longer there.
    """
    contents = SHIM_TEMPLATE.format(
        micromamba=str(micromamba), root=str(root), environment=environment
    )
    shim.parent.mkdir(parents=True, exist_ok=True)
    current = shim.read_text(encoding="utf-8") if shim.is_file() else None
    if current != contents:
        shim.write_text(contents, encoding="utf-8")
    mode = shim.stat().st_mode
    if not mode & stat_module.S_IXUSR:
        shim.chmod(mode | stat_module.S_IXUSR | stat_module.S_IXGRP | stat_module.S_IXOTH)
    return shim


class MetaPhlAnStage(Stage):
    """Profile microbial composition from clade-specific marker genes.

    Paired input has two forms in MetaPhlAn 4.2, and they are not equivalent.
    Passing both mates as one comma-separated value profiles every read. Passing
    them as -1 and -2 requires --subsampling_paired, which profiles only that
    many pairs and discards the rest. The first is the default here because
    subsampling is a speed control, not a better answer; the second is used only
    when a run explicitly asks for it.

    That choice also avoids a defect in MetaPhlAn 4.2.5: with -1/-2 and no
    explicit --mapout, init_mapout() calls os.stat() on the whole comma-joined
    temporary path and dies with FileNotFoundError. This stage always names its
    mapping output, so it never reaches that branch either way.
    """

    key = "metaphlan"
    title = "Taxonomic profiling (MetaPhlAn)"
    short_title = "Taxa"
    chip_title = "MetaPhlAn"
    environment_key = "taxonomy"
    required_databases = ("metaphlan_chocophlan",)

    def inputs(self, sample: Sample, context: RunContext) -> list[Path]:
        return context.workspace.host_removed_reads(sample)

    # ------------------------------------------------------------------
    def prepare(self, sample: Sample, context: RunContext, log: LogCallback) -> None:
        """Put the memory-mapped Bowtie2 shim in place before profiling."""
        if context.bowtie2_memory_mapped_shim is None:
            return
        if not context.shim_is_provided and context.micromamba_binary is None:
            return
        if not should_memory_map(context.total_memory()):
            log(
                f"This machine has {context.total_memory() / 1024 ** 3:.0f} GB of memory, "
                f"so Bowtie2 will load the index rather than memory-map it. That is "
                f"far faster: mapping is only worth its cost where the index cannot fit."
            )
            return
        if context.shim_is_provided:
            # Already in place, and pointing at the Micromamba that is actually
            # there. Writing the host's version would name a root the container
            # does not have.
            shim = context.bowtie2_memory_mapped_shim
        else:
            shim = write_memory_mapped_shim(
                context.bowtie2_memory_mapped_shim,
                context.micromamba_binary,
                context.micromamba_root,
                context.taxonomy_environment,
            )
        log(
            f"Bowtie2 will memory-map the index, through {shim}. This machine has "
            f"{context.total_memory() / 1024 ** 3:.0f} GB of memory and the index is "
            f"33 GB, so loading it outright would be killed."
        )
        # Said before the run rather than after it dies. MetaPhlAn spends its
        # first minute loading the marker table in silence, so a kill during
        # that window leaves nothing on screen to explain itself.
        for warning in memory_warnings():
            log(f"WARNING: {warning}")
        scale = profiling_scale_warning(
            self.inputs(sample, context), context.options.metaphlan_subsample_pairs
        )
        if scale:
            log(f"WARNING: {scale}")

    def threads(self, context: RunContext) -> str:
        """How many threads MetaPhlAn should use, given how the index is read.

        The ceiling applies only while the index is memory-mapped. There, a
        second thread does not add throughput: it adds a second access pattern
        evicting the first one's pages, and the run collapses - measured at
        21,851,582 major faults and 35.3 TB re-read over 6h37m that never
        finished. Where the index is loaded into memory that contention does not
        exist, and threads do what threads are supposed to do.
        """
        requested = max(1, int(context.threads))
        if should_memory_map(context.total_memory()):
            return str(min(requested, MAXIMUM_THREADS))
        return str(requested)

    def commands(self, sample: Sample, context: RunContext) -> list[StageCommand]:
        workspace = context.workspace
        return [
            self.profile_command(
                reads=self.inputs(sample, context),
                profile=workspace.taxonomic_profile(sample),
                mapout=workspace.taxonomic_map(sample),
                context=context,
                paired=sample.is_paired,
                label=sample.name,
            )
        ]

    def profile_command(
        self,
        reads: list[Path],
        profile: Path,
        mapout: Path,
        context: RunContext,
        paired: bool,
        label: str = "",
    ) -> StageCommand:
        """Build one MetaPhlAn invocation for the given reads.

        Separate from commands() so the standalone MetaPhlAn page can profile
        files the user picked directly, without a second copy of the flags. The
        two callers differ only in where the reads come from and where the
        results go; everything about how MetaPhlAn is run is decided here.
        """
        if context.metaphlan_database is None or not context.metaphlan_index:
            raise ValueError("No MetaPhlAn database is configured for taxonomic profiling.")
        subsample = context.options.metaphlan_subsample_pairs

        command = ["metaphlan"]
        if paired and subsample:
            # The only form that accepts a pair count, and the only one that
            # needs one: MetaPhlAn refuses -1/-2 without --subsampling_paired.
            command += ["-1", str(reads[0]), "-2", str(reads[1])]
        else:
            # One value, comma-separated for a pair. Every read is profiled.
            command.append(",".join(str(path) for path in reads))

        command += [
            "--input_type", "fastq",
            "--db_dir", str(context.metaphlan_database),
            "-x", context.metaphlan_index,
            "--offline",
            "--nproc", self.threads(context),
            # Progress reaches the log. Loading the index takes minutes during
            # which MetaPhlAn is otherwise silent, and a stage that prints
            # nothing for that long is indistinguishable from one that has hung
            # - which is exactly how a thrashing run was first mistaken for a
            # healthy one.
            "--verbose",
        ]
        if paired and subsample:
            command += ["--subsampling_paired", str(subsample)]
        elif subsample:
            command += ["--subsampling", str(subsample), "--mapping_subsampling"]

        # Only where the index has to be mapped. Pointing MetaPhlAn at the shim
        # on a machine with room to load the index would cost far more than it
        # saves - see MEMORY_MAPPING_THRESHOLD_BYTES.
        if context.bowtie2_memory_mapped_shim is not None and should_memory_map(
            context.total_memory()
        ):
            command += ["--bowtie2_exe", str(context.bowtie2_memory_mapped_shim)]

        command += [
            # Always named, which is also what keeps MetaPhlAn 4.2.5 out of the
            # init_mapout() branch that cannot handle a comma-joined input path.
            "--mapout", str(mapout),
            "-o", str(profile),
        ]

        layout = "paired-end" if paired else "single-end"
        scope = f", {subsample} pair(s)" if subsample else ""
        named = f"{label} " if label else ""
        return StageCommand(
            description=f"Profile {named}({layout}{scope}) with MetaPhlAn",
            command=command,
            environment_key=self.environment_key,
        )

    def outputs(self, sample: Sample, context: RunContext) -> list[Path]:
        workspace = context.workspace
        return [workspace.taxonomic_profile(sample), workspace.taxonomic_map(sample)]

    def validate(self, sample: Sample, context: RunContext) -> ValidationResult:
        workspace = context.workspace
        result = ValidationResult()
        profile = workspace.taxonomic_profile(sample)
        if not check_exists(result, profile, minimum_bytes=16):
            return result

        # MetaPhlAn writes a header-only profile when nothing is classified,
        # and exits zero either way. For a metagenome that almost always means
        # the run did not do what was intended - too few reads survived host
        # removal, the reads are shorter than MetaPhlAn's 70 bp minimum, or the
        # wrong database was selected - so it is treated as a failure rather
        # than a result. The detail says which of the two this is, because "no
        # organisms found" and "the tool crashed" look identical otherwise.
        rows = count_data_rows(profile)
        result.add(
            "profile contains taxonomic assignments",
            rows > 0,
            f"{rows} clade row(s)"
            if rows
            else "MetaPhlAn ran and wrote a valid profile, but classified no "
                 "reads. Check how many reads survived host removal, that they "
                 "are at least 70 bp, and that the database matches the reads",
        )
        check_exists(result, workspace.taxonomic_map(sample), minimum_bytes=16)
        return result
