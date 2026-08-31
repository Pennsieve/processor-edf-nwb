#!/bin/sh
set -eu

: "${INPUT_DIR:?INPUT_DIR must be set}"
: "${OUTPUT_DIR:?OUTPUT_DIR must be set}"

# The container may run as a non-root user, in which case /app and the default
# HOME are not writable. Keep every runtime write inside one scratch directory:
# the rendered config, plus the caches that neuroconv's dependencies create when
# they are imported.
SCRATCH_DIR="${TMPDIR:-/tmp}/edf-nwb"
export HOME="$SCRATCH_DIR"
export XDG_CACHE_HOME="$SCRATCH_DIR/cache"
mkdir -p "$XDG_CACHE_HOME"

EDF_FILE=$(find "$INPUT_DIR" -maxdepth 1 -type f -iname '*.edf' | head -n 1)
if [ -z "$EDF_FILE" ]; then
    echo "No EDF file found in INPUT_DIR=$INPUT_DIR" >&2
    exit 1
fi

INPUT_FILE=$(basename "$EDF_FILE")
OUTPUT_FILE="${INPUT_FILE%.*}.nwb"
export INPUT_FILE OUTPUT_FILE
echo "INPUT_FILE=${INPUT_FILE}"
echo "OUTPUT_FILE=${OUTPUT_FILE}"

# Clinical EDF exporters autoscale each channel independently, which yields a
# slightly different offset per channel. An NWB ElectricalSeries stores only a
# single scalar offset, so those recordings can only be written by folding each
# channel's gain and offset into the samples and storing float physical values.
# See https://neuroconv.readthedocs.io/en/main/how_to/handle_heterogeneous_offsets.html
# Override with DATA_REPRESENTATION=digital_counts to keep the raw integer
# samples, which only works when every channel shares the same offset.
export DATA_REPRESENTATION="${DATA_REPRESENTATION:-physical_units}"
echo "DATA_REPRESENTATION=${DATA_REPRESENTATION}"

# Record how the naive EDF start time was interpreted, so a reader can tell an
# assumed timezone from a known one. The Dockerfile pins TZ=UTC; anything else
# means the deployment supplied the recording site.
SESSION_TZ="${TZ:-UTC}"
if [ "$SESSION_TZ" = "UTC" ] || [ "$SESSION_TZ" = "Etc/UTC" ]; then
    SESSION_TIME_PROVENANCE="EDF records the recording start time as a local wall clock without timezone information. No recording-site timezone was configured for this conversion, so session_start_time was interpreted as UTC. The date and time digits from the EDF header are preserved verbatim, so if the recording-site timezone is established later, session_start_time can be corrected by a uniform shift."
else
    SESSION_TIME_PROVENANCE="EDF records the recording start time as a local wall clock without timezone information. This conversion was configured with TZ=${SESSION_TZ}, so session_start_time was interpreted in that timezone."
fi
export SESSION_TIME_PROVENANCE
echo "TZ=${SESSION_TZ}"

CONFIG_FILE="$SCRATCH_DIR/neuroconv_edf.yml"
envsubst < /app/neuroconv_edf.template.yml > "$CONFIG_FILE"

neuroconv "$CONFIG_FILE" --overwrite \
    --data-folder-path "$INPUT_DIR" \
    --output-folder-path "$OUTPUT_DIR"
