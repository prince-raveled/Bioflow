#!/usr/bin/env bash
# Profile a sample against the installed MetaPhlAn database, and report whether
# the stage works end to end.
#
# Run this from a plain terminal, NOT from inside an editor. Alignment against
# the ChocoPhlAn index holds about 10 GB resident; if the editor launched it,
# the kernel's out-of-memory killer reaps the editor along with the alignment.
#
#   scripts/check_metaphlan.sh                  # synthetic reads
#   scripts/check_metaphlan.sh reads.fastq.gz   # your own sample
set -uo pipefail

root="${BIOFLOW_DATA_DIR:-$HOME/.local/share/bioflow}"
index="mpa_vJan25_CHOCOPhlAnSGB_202503"
database="$root/databases/metaphlan"
work="$(mktemp -d -t bioflow-mpa-check-XXXXXX)"
trap 'rm -rf "$work"' EXIT

fail() { printf '\nFAILED: %s\n' "$1" >&2; exit 1; }

# ---------------------------------------------------------------- prerequisites
[ -x "$root/bin/micromamba" ] || fail "Micromamba is not installed at $root/bin/micromamba"
for suffix in .pkl .1.bt2l .2.bt2l .3.bt2l .4.bt2l .rev.1.bt2l .rev.2.bt2l; do
    [ -f "$database/$index$suffix" ] || fail "the database is incomplete: $index$suffix is missing"
done
echo "Database: complete (7/7 required files in $database)"

# ------------------------------------------------------------------- memory
# MemAvailable is the honest figure: installed RAM a browser is already holding
# is not RAM this alignment can use.
available=$(awk '/^MemAvailable:/ {printf "%d", $2/1024}' /proc/meminfo)
needed=10240
printf 'Memory:   %d MB available, %d MB needed\n' "$available" "$needed"
if [ "$available" -lt "$needed" ]; then
    echo
    echo "  Not enough free memory. Close your editor and browser, then re-run."
    echo "  Continuing anyway would very likely end in the kernel killing this"
    echo "  process - and whatever else shares its memory scope."
    exit 1
fi

# -------------------------------------------------------------------- input
if [ "$#" -ge 1 ]; then
    reads="$1"
    [ -f "$reads" ] || fail "no such file: $reads"
    synthetic=no
    echo "Input:    $reads"
else
    reads="$work/reads.fastq"
    python3 - "$reads" <<'PY'
import random, sys
random.seed(7)
with open(sys.argv[1], "w") as handle:
    for index in range(2000):
        sequence = "".join(random.choice("ACGT") for _ in range(150))
        handle.write(f"@read{index}\n{sequence}\n+\n{'I' * 150}\n")
PY
    synthetic=yes
    echo "Input:    2,000 synthetic reads (pass a FASTQ to profile a real sample)"
fi

# ------------------------------------------------------------------ profile
echo
echo "Profiling. The index is 33 GB, so the first minutes are spent loading it."
start=$(date +%s)
"$root/bin/micromamba" run -r "$root/micromamba-root" -n bioflow-taxonomy \
    metaphlan "$reads" \
        --input_type fastq \
        --db_dir "$database" \
        -x "$index" \
        --offline \
        --nproc 4 \
        -o "$work/profile.txt"
status=$?
elapsed=$(( $(date +%s) - start ))

echo
if [ "$status" -lt 0 ] || [ "$status" -ge 128 ]; then
    fail "MetaPhlAn was killed (status $status), almost certainly out of memory"
fi
[ "$status" -eq 0 ] || fail "MetaPhlAn exited with status $status"
[ -s "$work/profile.txt" ] || fail "MetaPhlAn succeeded but wrote no profile"

# ------------------------------------------------------------------ verdict
taxa=$(grep -cv '^#' "$work/profile.txt")
printf 'Completed in %dm %02ds\n' "$((elapsed / 60))" "$((elapsed % 60))"
echo "Profile:  $taxa row(s)"
echo
head -4 "$work/profile.txt"
echo
if [ "$taxa" -eq 0 ] && [ "$synthetic" = yes ]; then
    echo "PASSED as a tool check. Zero taxa is the correct answer here: random"
    echo "sequence matches no marker gene. This proves the environment, the"
    echo "database and the offline alignment all work."
    echo
    echo "Note: the pipeline itself treats a zero-taxa profile as a FAILED stage,"
    echo "because for a real metagenome it means something went wrong upstream."
    echo "So use a real FASTQ for the end-to-end run, not synthetic reads."
elif [ "$taxa" -eq 0 ]; then
    echo "Ran cleanly, but found nothing in this sample. That can be genuine (host"
    echo "reads only, very low depth) rather than a fault."
else
    echo "PASSED. MetaPhlAn is working: $taxa taxa detected."
fi
