#!/bin/sh

EDF_FILE=$(find $INPUT_DIR -maxdepth 1 -type f -iname '*.edf' | head -n 1)

if [ -n "$EDF_FILE" ]; then
    export INPUT_FILE=$(basename "$EDF_FILE")
    echo "INPUT_FILE=${INPUT_FILE}"
else
    echo "No EDF files found in INPUT_DIR=$INPUT_DIR"
fi

BASE_NAME="${INPUT_FILE%.*}"
export OUTPUT_FILE="${BASE_NAME}.nwb"
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

envsubst < /app/neuroconv_edf.template.yml > /app/neuroconv_edf.yml

neuroconv /app/neuroconv_edf.yml --overwrite \
    --data-folder-path $INPUT_DIR \
    --output-folder-path $OUTPUT_DIR
