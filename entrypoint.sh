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

envsubst < /app/neuroconv_edf.template.yml > /app/neuroconv_edf.yml

neuroconv /app/neuroconv_edf.yml --overwrite \
    --data-folder-path $INPUT_DIR \
    --output-folder-path $OUTPUT_DIR
