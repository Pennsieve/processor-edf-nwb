# EDF to NWB Processor

Converts one EDF or EDF+ recording into an NWB file.

Signals recorded in volts are written into an NWB `ElectricalSeries` per sample rate,
using neuroconv's EDFRecordingInterface. Every other signal becomes a `TimeSeries`
with its recorded unit untouched. Set `NON_NEURAL_CHANNELS` to a comma-separated list
of channel labels to write as `TimeSeries` even when the EDF has them labeled with a volt unit.

All supported parameters are described in `app.yml`.

## Usage

Convert one file directly:

```bash
python convert_edf.py recording.edf recording.nwb
```

With no arguments the script follows the processor convention and converts the
first `.edf` file in `INPUT_DIR` and writes `OUTPUT_DIR/<input>.nwb`. `make run`
sets `INPUT_DIR=data/input/` and `OUTPUT_DIR=data/output/`.
