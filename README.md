# EDF to NWB Processor

Converts one EDF or EDF+ recording into an NWB file.

Signals recorded in volts are written into an NWB `ElectricalSeries` per sample rate,
using neuroconv's EDFRecordingInterface. Every other signal becomes a `TimeSeries`
with its recorded unit untouched. Set `NON_NEURAL_CHANNELS` to a comma-separated list
of channel labels to write as `TimeSeries` even when the EDF has them labeled with a volt unit.

All supported parameters are described in `app.yml`.

Running `make run` locally will run the processor on an `.edf` file in `data/input`
and output an `.nwb` file into `data/output`.
