# BioFlow's analysis tools, and nothing else.
#
# The image holds software: Micromamba and the same three environments the
# native installation builds, under the same names, so a command differs
# between the two backends only by the prefix that enters the container.
#
# It deliberately does NOT hold reference data. The MetaPhlAn database is ~51 GB
# and GRCh38 is several more; baking either would make a fastp bugfix a 51 GB
# re-download, would stop the two backends sharing one copy, and would prevent a
# lab pointing several machines at the same reference store. Databases, reads
# and results are mounted at run time.
#
# Build:  docker/build.sh
# Run:    handled by BioFlow's container backend, which supplies every mount,
#         --network=none, a writable tmpfs on /tmp, and the user mapping.

# ---------------------------------------------------------------------------
# Fetch Micromamba in a throwaway stage, so curl and its dependencies never
# reach the shipped image.
# ---------------------------------------------------------------------------
FROM debian:bookworm-slim AS fetch

ARG MICROMAMBA_VERSION=2.9.0

RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl bzip2 \
 && rm -rf /var/lib/apt/lists/* \
 && curl -Ls "https://micro.mamba.pm/api/micromamba/linux-64/${MICROMAMBA_VERSION}" \
      | tar -xj -C /tmp bin/micromamba \
 && /tmp/bin/micromamba --version

# ---------------------------------------------------------------------------
# The image itself.
# ---------------------------------------------------------------------------
FROM debian:bookworm-slim

# bash is not optional: the bowtie2-mm shim's shebang is /usr/bin/env bash.
# procps gives the tools a working ps, which some wrappers call.
RUN apt-get update \
 && apt-get install -y --no-install-recommends bash ca-certificates procps \
 && rm -rf /var/lib/apt/lists/*

COPY --from=fetch /tmp/bin/micromamba /usr/local/bin/micromamba

ENV MAMBA_ROOT_PREFIX=/opt/conda \
    LC_ALL=C.UTF-8 \
    LANG=C.UTF-8

# One environment per layer, in the order they are least likely to change, so
# bumping one tool does not invalidate the others' packages.
#
# Channel order matches the native installation exactly (-c conda-forge
# -c bioconda); solving them differently is how two installations that claim the
# same versions end up with different builds.
COPY environments/ /opt/bioflow/environments/

RUN micromamba create -y -r /opt/conda -n bioflow-hostrem \
      -c conda-forge -c bioconda \
      $(grep -v '^#' /opt/bioflow/environments/hostrem.txt | tr '\n' ' ') \
 && micromamba clean --all --yes

RUN micromamba create -y -r /opt/conda -n bioflow-qc \
      -c conda-forge -c bioconda \
      $(grep -v '^#' /opt/bioflow/environments/qc.txt | tr '\n' ' ') \
 && micromamba clean --all --yes

RUN micromamba create -y -r /opt/conda -n bioflow-taxonomy \
      -c conda-forge -c bioconda \
      $(grep -v '^#' /opt/bioflow/environments/taxonomy.txt | tr '\n' ' ') \
 && micromamba clean --all --yes

COPY bin/bowtie2-mm /opt/bioflow/bin/bowtie2-mm
RUN chmod 0755 /opt/bioflow/bin/bowtie2-mm

# Record what was actually solved. An explicit list names every package by URL
# and build, which is what makes a rebuild reproducible rather than merely
# similar; the version pins above only constrain the solve.
RUN for env in bioflow-qc bioflow-hostrem bioflow-taxonomy; do \
      micromamba env export -r /opt/conda -n "$env" --explicit \
        > "/opt/bioflow/environments/${env}.lock" || exit 1; \
    done \
 && chmod 0444 /opt/bioflow/environments/*.lock

# Verify inside the build, so a broken image cannot be published. Each tool is
# run the way BioFlow runs it, through micromamba, rather than merely existing.
RUN micromamba run -r /opt/conda -n bioflow-qc fastqc --version \
 && micromamba run -r /opt/conda -n bioflow-qc fastp --version \
 && micromamba run -r /opt/conda -n bioflow-qc multiqc --version \
 && micromamba run -r /opt/conda -n bioflow-hostrem bowtie2 --version | head -1 \
 && micromamba run -r /opt/conda -n bioflow-hostrem samtools --version | head -1 \
 && micromamba run -r /opt/conda -n bioflow-hostrem seqkit version \
 && micromamba run -r /opt/conda -n bioflow-taxonomy metaphlan --version \
 && micromamba run -r /opt/conda -n bioflow-taxonomy bowtie2 --version | head -1

# Non-root by default. At run time the backend maps this to the invoking user
# (--userns=keep-id under rootless Podman, --user under Docker) so results are
# owned by the person who started the analysis rather than by root.
RUN groupadd --gid 1000 bioflow \
 && useradd --uid 1000 --gid 1000 --create-home --shell /bin/bash bioflow

# Nothing under /opt is written to at run time, so the container runs with
# --read-only and a tmpfs on /tmp. That is enforced by the runtime over the
# whole root filesystem; stripping write bits here as well would add nothing,
# and `chmod -R` over the conda tree is expensive in a way that is easy to miss:
# it rewrites the metadata of every file, which makes the overlay copy each one
# into a new layer. Doing it cost 11.3 GB on top of a 6.8 GB image.

USER 1000:1000
WORKDIR /

# No ENTRYPOINT on purpose. BioFlow supplies the whole command, and an
# entrypoint that rewrote arguments would break the parity between what the
# record says ran and what actually ran.
CMD ["/bin/bash", "-lc", "echo 'BioFlow tools image. BioFlow supplies the command.' && exit 64"]

LABEL org.opencontainers.image.title="BioFlow analysis tools" \
      org.opencontainers.image.description="FastQC, fastp, MultiQC, Bowtie2 and MetaPhlAn in BioFlow's Micromamba environments. Reference databases are mounted, never included." \
      org.opencontainers.image.licenses="MIT" \
      io.bioflow.environments="bioflow-qc,bioflow-hostrem,bioflow-taxonomy" \
      io.bioflow.mamba-root="/opt/conda" \
      io.bioflow.shim="/opt/bioflow/bin/bowtie2-mm"
