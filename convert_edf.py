"""Convert one EDF or EDF+ recording into an NWB file.

Voltage signals, those whose physical dimension is a volt unit, become one
ElectricalSeries per sample rate through neuroconv's EDF interface. Every other
signal becomes a TimeSeries in acquisition carrying its own unit. The
NON_NEURAL_CHANNELS environment variable names further labels to treat as
non-voltage, for exporters that stamp microvolts on respiratory, position, or
auxiliary inputs.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import numpy as np
import pyedflib
from neuroconv.datainterfaces import EDFRecordingInterface
from neuroconv.tools.nwb_helpers import (
    configure_and_write_nwbfile,
    make_nwbfile_from_metadata,
)
from neuroconv.utils import dict_deep_update
from pynwb import TimeSeries

VOLTAGE_UNITS = frozenset({"uv", "mv", "v"})
"""Physical dimensions, lowercased, that an ElectricalSeries can carry."""


def parse_label_list(value: str) -> set[str]:
    """Return the labels of a comma-separated list, trimmed, dropping empty entries."""
    return {label.strip() for label in value.split(",") if label.strip()}


def is_voltage_signal(header: dict, forced_non_voltage: set[str]) -> bool:
    """Return whether a signal header describes a voltage signal for ElectricalSeries.

    The physical dimension decides, case-insensitively, unless the label is in
    forced_non_voltage.
    """
    unit = str(header["dimension"]).strip().lower()
    return unit in VOLTAGE_UNITS and header["label"] not in forced_non_voltage


def signal_rate_hz(header: dict) -> float:
    """Return a signal's sample rate. pyedflib names the field differently across versions."""
    return float(header.get("sample_frequency") or header["sample_rate"])


def describe_signal(header: dict) -> str:
    """Return the EDF transducer and prefilter fields as a TimeSeries description."""
    transducer = str(header.get("transducer", "")).strip() or "n/a"
    prefilter = str(header.get("prefilter", "")).strip() or "n/a"
    return f"EDF signal. transducer: {transducer}; prefilter: {prefilter}"


def voltage_interfaces(
    edf_path: Path, voltage_labels: set[str]
) -> list[tuple[EDFRecordingInterface, float]]:
    """Return one EDF interface per stream holding a voltage signal, with its rate.

    neo groups an EDF's signals into one stream per sample rate. Each interface
    skips every label in its stream that is not a voltage signal, so the
    ElectricalSeries it writes holds voltage channels only.
    """
    interfaces: list[tuple[EDFRecordingInterface, float]] = []
    for stream_name in EDFRecordingInterface.get_stream_names(edf_path):
        probe = EDFRecordingInterface(file_path=edf_path, stream_name=stream_name)
        stream_labels = [str(label) for label in probe.recording_extractor.get_channel_ids()]
        if not any(label in voltage_labels for label in stream_labels):
            continue
        rate_hz = float(probe.recording_extractor.get_sampling_frequency())
        skip = [label for label in stream_labels if label not in voltage_labels]
        interface = EDFRecordingInterface(
            file_path=edf_path,
            stream_name=stream_name,
            channels_to_skip=skip or None,
            metadata_key=f"edf_{rate_hz:g}hz",
        )
        interfaces.append((interface, rate_hz))
    return interfaces


def series_name(rate_hz: float, single_rate: bool) -> str:
    """Return the ElectricalSeries name for a rate: plain when the file has one rate."""
    return "ElectricalSeries" if single_rate else f"ElectricalSeries{rate_hz:g}Hz"


def convert(
    edf_path: Path,
    nwb_path: Path,
    *,
    data_representation: str,
    forced_non_voltage: set[str],
    provenance: str | None,
) -> None:
    """Write nwb_path from edf_path, replacing any file already there."""
    reader = pyedflib.EdfReader(str(edf_path))
    try:
        headers = reader.getSignalHeaders()
    finally:
        # EDFlib refuses to reopen a file it holds open, and neuroconv opens it next.
        reader.close()

    voltage_labels = {
        header["label"]
        for header in headers
        if is_voltage_signal(header, forced_non_voltage)
    }
    other_indices = [
        index
        for index, header in enumerate(headers)
        if header["label"] not in voltage_labels
    ]

    interfaces = voltage_interfaces(edf_path, voltage_labels)

    # Session and subject metadata come from the header whichever stream reads it.
    metadata = EDFRecordingInterface(file_path=edf_path).get_metadata()
    for interface, rate_hz in interfaces:
        metadata = dict_deep_update(metadata, interface.get_metadata())
        metadata["Ecephys"]["ElectricalSeries"][interface.metadata_key]["name"] = (
            series_name(rate_hz, single_rate=len(interfaces) == 1)
        )
    if provenance:
        metadata["NWBFile"]["data_collection"] = provenance

    nwbfile = make_nwbfile_from_metadata(metadata)
    for interface, _ in interfaces:
        interface.add_to_nwbfile(
            nwbfile, metadata=metadata, data_representation=data_representation
        )

    reader = pyedflib.EdfReader(str(edf_path))
    try:
        for index in other_indices:
            header = headers[index]
            nwbfile.add_acquisition(
                TimeSeries(
                    name=header["label"],
                    data=reader.readSignal(index).astype(np.float32),
                    unit=str(header["dimension"]).strip() or "n/a",
                    rate=signal_rate_hz(header),
                    starting_time=0.0,
                    conversion=1.0,
                    description=describe_signal(header),
                )
            )
    finally:
        reader.close()

    if nwb_path.exists():
        nwb_path.unlink()
    configure_and_write_nwbfile(nwbfile, nwbfile_path=nwb_path, backend="hdf5")

    print(
        f"wrote {nwb_path}: {len(voltage_labels)} voltage channels in "
        f"{len(interfaces)} ElectricalSeries, {len(other_indices)} TimeSeries"
    )
    for index in other_indices:
        header = headers[index]
        print(f"  TimeSeries {header['label']} [{str(header['dimension']).strip() or 'n/a'}]")


def main(argv: list[str]) -> int:
    """Run one conversion from the command line."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("edf", type=Path, help="EDF or EDF+ recording to convert")
    parser.add_argument("nwb", type=Path, help="NWB file to write")
    args = parser.parse_args(argv)

    convert(
        args.edf,
        args.nwb,
        data_representation=os.environ.get("DATA_REPRESENTATION", "physical_units"),
        forced_non_voltage=parse_label_list(os.environ.get("NON_NEURAL_CHANNELS", "")),
        provenance=os.environ.get("SESSION_TIME_PROVENANCE") or None,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
