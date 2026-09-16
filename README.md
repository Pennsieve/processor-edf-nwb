# EDF to NWB Processor

Converts one EDF or EDF+ recording into an NWB file.

Signals recorded in volts are written into an NWB `ElectricalSeries` per sample rate,
using neuroconv's EDFRecordingInterface. Every other signal becomes a `TimeSeries`
with its recorded unit untouched. Set `NON_NEURAL_CHANNELS` to a comma-separated list
of channel labels to write as `TimeSeries` even when the EDF has them labeled with a volt unit.

All supported parameters are described in `app.yml`.

## Usage

Convert one file directly, from the repository root or anywhere `edf_nwb` is importable:

```bash
python -m edf_nwb.main recording.edf recording.nwb
```

With no arguments the converter follows the processor convention instead: it converts
the first `.edf` file in `INPUT_DIR` and writes `OUTPUT_DIR/<input>.nwb`. This is how the
container runs.

```bash
make run        # docker-compose build + up, against data/input and data/output
```

## Layout

`edf_nwb/` holds the package; `edf_nwb/main.py` is both the conversion logic and the
command-line entry point.
