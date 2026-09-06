# BioFlow analysis image

Software only. FastQC, fastp, MultiQC, Bowtie2 and MetaPhlAn, in the same three
Micromamba environments the native installation builds, under the same names.

## What is deliberately not here

Reference databases, reads and results are **mounted at run time**, never built
in:

| Asset | Approximate size | Why it stays outside |
|---|---|---|
| MetaPhlAn database | ~51 GB | A layer is immutable, so a fastp bugfix would become a 51 GB re-download. |
| GRCh38 Bowtie2 index | ~4 GB | The native backend needs the same copy; baking it means two. |
| FASTQ input, results | user data | Never belongs in an image. |

Databases are also versioned independently of the software, and a lab that puts
one copy on shared storage can point every machine at it. Both BioFlow backends
read the same reference store.

## Layout inside the image

```
/usr/local/bin/micromamba        the same version the native installer fetches
/opt/conda/envs/bioflow-qc       fastqc, fastp, multiqc
/opt/conda/envs/bioflow-hostrem  bowtie2, samtools, bwa, fastp, pigz, seqkit
/opt/conda/envs/bioflow-taxonomy metaphlan, bowtie2
/opt/bioflow/bin/bowtie2-mm      Bowtie2 with --mm, for a mapped index
/opt/bioflow/environments/*.txt  the pinned specs this image was solved from
/opt/bioflow/environments/*.lock explicit package lists, as actually solved
```

`/opt` is made read-only at build time. The container needs no writable
location of its own beyond a tmpfs on `/tmp`.

## Versions

Pinned in `environments/*.txt` to what the native installation resolved to, so
that a container result can be compared against a native one without the tools
being a variable. The `.lock` files inside the image record what the solver
actually chose, down to the build string and URL.

## Building

```
docker/build.sh
```

Podman is preferred and is what Fedora and RHEL ship: no daemon, and rootless it
needs no group whose membership is equivalent to root. Docker works identically;
the image is OCI, not Docker-specific.

Override the name with `BIOFLOW_IMAGE` and `BIOFLOW_IMAGE_TAG`.

## Running

BioFlow's container backend supplies the command and every mount. The image has
no `ENTRYPOINT` on purpose: BioFlow passes the whole command, and an entrypoint
that rewrote arguments would break the correspondence between what the run
record says ran and what actually ran.

Run by digest rather than tag. A tag can be moved to different bytes; a digest
cannot, and the digest is what the run record stores.

## Why Micromamba is inside

The obvious simplification is to put the tools on `PATH` and drop Micromamba.
Keeping it, with the same environment names, means the inner command is
identical in both backends:

```
native      micromamba run -r <data root>/micromamba-root -n bioflow-qc fastqc ...
container   podman run ... IMAGE \
            micromamba run -r /opt/conda            -n bioflow-qc fastqc ...
                            └──────────── identical from here ────────────┘
```

That is what lets one set of tests cover both, and what makes a native result
and a container result comparable argument by argument.

## Two things the runtime must get right

Both were found by running this image, not by reading about it.

### SELinux

Fedora and RHEL - the systems Podman ships on by default - enforce SELinux
labels on bind mounts. Without help, every mount is `Permission denied` even
though it is mounted: the container sees the mountpoint and cannot read it.

The usual answer is to suffix a mount with `:z` or `:Z`, which relabels the host
directory. **BioFlow must not do that.** Relabelling walks the ~51 GB MetaPhlAn
database, and `:Z` applies a private label that would stop the native backend
reading its own database afterwards. Modifying reference data to run a container
is not acceptable.

`--security-opt label=disable` turns off SELinux confinement for the container
alone and leaves every host label untouched, which is what the backend uses.
The rest of the posture still applies: non-root, `--cap-drop=ALL`,
`--no-new-privileges`, `--read-only`, `--network=none`, and only the mounts
named for that command.

### A tmpfs on /tmp hides anything mounted beneath it

The container gets a writable tmpfs at `/tmp`. With identity mounts - host path
equals container path - a host directory *under* `/tmp` is shadowed by that
tmpfs and vanishes. It mounts, and it is empty.

So `/tmp` belongs on the list of paths that cannot be identity-mounted,
alongside `/usr`, `/etc`, `/var`, `/bin`, `/lib`, `/opt`, `/proc`, `/sys`,
`/dev` and `/root`. Real input and results live under `/home`, `/mnt`,
`/media`, `/run/media`, `/data` or `/srv`, none of which collide.

## Size

About 6.9 GB uncompressed. MetaPhlAn's Python stack is roughly 4 GB of that and
FastQC brings a JRE; neither is avoidable while the tools match the native
installation exactly.

One warning from building it: `chmod -R` across the conda tree rewrites every
file's metadata, and the overlay copies each one into a new layer. An earlier
revision did that to strip write bits, and it cost **11.3 GB on top of a 6.8 GB
image** for no benefit, because `--read-only` is enforced by the runtime over
the whole filesystem anyway. Avoid recursive metadata changes over `/opt/conda`.
