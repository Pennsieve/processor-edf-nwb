"""Convert one EDF or EDF+ recording into an NWB file.

Voltage signals, those whose physical dimension is a volt unit, are written as 
ElectricalSeries, per sample rate, using neuroconv's EDF interface, in volts.
Every other signal becomes a TimeSeries in its given unit.

Run as python -m edf_nwb.main with an EDF path and an NWB path, or with neither
to follow the Pennsieve processor convention where the first .edf file in
INPUT_DIR is converted and the result is written to OUTPUT_DIR under the same
name with an .nwb suffix. The container runs the no-argument form.
Parameters arrive as environment variables, each described in app.yml:
DATA_REPRESENTATION, NON_NEURAL_CHANNELS, TZ, and LOG_LEVEL.
"""

from __future__ import annotations

import argparse
import faulthandler
import importlib.metadata
import logging
import os
import signal
import sys
import threading
import time
import warnings
from collections.abc import Mapping
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

UTC_NAMES = frozenset({"UTC", "Etc/UTC"})
"""TZ values that mean no recording-site timezone was supplied."""

HEARTBEAT_SECONDS = 15.0
"""Interval between debug-level progress lines during a long-running stage."""

# python -m names this module __main__, so the logger is named for the package.
log = logging.getLogger("edf_nwb")

_stage = "starting"
"""The step convert is in, reported by heartbeats and the failure log."""


def configure_logging(level_name: str) -> None:
    """Send the processor's logs and Python warnings to stderr, one flushed line per record.

    level_name sets the processor's own logger. Libraries stay at WARNING because
    pynwb and hdmf log every object they map at DEBUG, which buries the processor's
    lines. faulthandler prints the Python stack if native code crashes, which
    would otherwise end the process without a traceback.
    """
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    logging.basicConfig(level=logging.WARNING, handlers=[handler], force=True)
    level = logging.getLevelNamesMapping().get(level_name.upper())
    if level is None:
        log.warning("LOG_LEVEL=%s is not a logging level; using INFO", level_name)
        level = logging.INFO
    log.setLevel(level)
    warnings.formatwarning = format_warning
    logging.captureWarnings(True)
    faulthandler.enable()


def format_warning(message, category, filename, lineno, line=None) -> str:
    """Return a warning as one log line, without the source line Python appends."""
    return f"{category.__name__}: {message} ({filename}:{lineno})"


def memory_limit_bytes() -> int | None:
    """Return the container's cgroup memory limit, or None when it has none."""
    for path in (
        "/sys/fs/cgroup/memory.max",  # cgroup v2
        "/sys/fs/cgroup/memory/memory.limit_in_bytes",  # cgroup v1
    ):
        try:
            value = Path(path).read_text().strip()
        except OSError:
            continue
        # v2 writes "max" for no limit; v1 writes a number near 2**63.
        return int(value) if value.isdigit() and int(value) < 2**62 else None
    return None


def memory_usage() -> str:
    """Return the process's resident and peak resident memory, or n/a off Linux."""
    try:
        status = Path("/proc/self/status").read_text()
    except OSError:
        return "rss n/a"
    kib = {
        name: int(value.split()[0])
        for name, _, value in (line.partition(":") for line in status.splitlines())
        if name in ("VmRSS", "VmHWM")
    }
    return f"rss {kib['VmRSS'] / 2**20:.2f} GiB, peak {kib['VmHWM'] / 2**20:.2f} GiB"


def enter_stage(name: str) -> None:
    """Record that convert has moved on to the step name, and log it."""
    global _stage
    _stage = name
    log.info("%s", name)
    log.debug("memory at start of %s: %s", name, memory_usage())


def start_heartbeat(interval: float) -> None:
    """Log the current stage and memory every interval seconds from a daemon thread.

    If heartbeats keep coming until the log ends, the process was stopped or
    hung. If they stop while memory climbs toward the limit, it was OOM-killed.
    """
    def beat() -> None:
        while True:
            time.sleep(interval)
            log.debug("still in %s: %s", _stage, memory_usage())

    threading.Thread(target=beat, name="heartbeat", daemon=True).start()


def log_stack_on_sigterm(signum: int, _frame: object) -> None:
    """Log where the process was when it was told to stop, then exit.

    Fargate sends SIGTERM when it stops a task, then SIGKILL after a grace
    period. As the container's PID 1, the process ignores SIGTERM unless it
    installs a handler, and is then killed with nothing logged.
    """
    log.error("received SIGTERM during %s (%s); stack follows", _stage, memory_usage())
    faulthandler.dump_traceback(all_threads=True)
    sys.exit(128 + signum)


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


def session_time_provenance(tz_name: str) -> str:
    """Return the data_collection note recording how the EDF start time was read.

    EDF stores the start time as a naive local wall clock, and pynwb stamps the
    process's local timezone on it. The Dockerfile pins TZ=UTC, so any other
    value means the deployment supplied the recording site.
    """
    if tz_name in UTC_NAMES:
        return (
            "EDF records the recording start time as a local wall clock without "
            "timezone information. No recording-site timezone was configured for "
            "this conversion, so session_start_time was interpreted as UTC. The "
            "date and time digits from the EDF header are preserved verbatim, so "
            "if the recording-site timezone is established later, "
            "session_start_time can be corrected by a uniform shift."
        )
    return (
        "EDF records the recording start time as a local wall clock without "
        f"timezone information. This conversion was configured with TZ={tz_name}, "
        "so session_start_time was interpreted in that timezone."
    )


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
        rate_hz = float(probe.recording_extractor.get_sampling_frequency())
        if not any(label in voltage_labels for label in stream_labels):
            log.debug("neo stream %r at %g Hz has no voltage signals; not opened", stream_name, rate_hz)
            continue
        skip = [label for label in stream_labels if label not in voltage_labels]
        log.debug(
            "neo stream %r at %g Hz: %d voltage channels, skipping %s",
            stream_name, rate_hz, len(stream_labels) - len(skip), skip or "none",
        )
        interface = EDFRecordingInterface(
            file_path=edf_path,
            stream_name=stream_name,
            channels_to_skip=skip or None,
            metadata_key=f"edf_{rate_hz:g}hz",
        )
        interfaces.append((interface, rate_hz))
    return interfaces


def header_metadata(edf_path: Path) -> dict:
    """Return neuroconv's session and subject metadata read from the EDF header.

    neo splits an EDF into one stream per sample rate and refuses to open a
    reader without a stream when the file has several, so the header is read
    through the first stream. Session and subject metadata are the same
    whichever stream reads them.
    """
    stream_name = next(iter(EDFRecordingInterface.get_stream_names(edf_path)))
    return EDFRecordingInterface(file_path=edf_path, stream_name=stream_name).get_metadata()


def series_name(rate_hz: float, single_rate: bool) -> str:
    """Return the ElectricalSeries name for a rate: plain when the file has one rate."""
    return "ElectricalSeries" if single_rate else f"ElectricalSeries{rate_hz:g}Hz"


def convert(
    edf_path: Path,
    nwb_path: Path,
    *,
    data_representation: str,
    forced_non_voltage: set[str],
    provenance: str,
) -> None:
    """Write nwb_path from edf_path, replacing any file already there."""
    started = time.monotonic()
    enter_stage("reading EDF signal headers")
    reader = pyedflib.EdfReader(str(edf_path))
    try:
        headers = reader.getSignalHeaders()
        duration_s = reader.getFileDuration()
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
    log.info(
        "EDF holds %d signals over %.2f h: %d voltage, %d other",
        len(headers), duration_s / 3600, len(voltage_labels), len(other_indices),
    )
    for header in headers:
        log.debug(
            "signal %r [%s] at %g Hz -> %s",
            header["label"], str(header["dimension"]).strip() or "n/a", signal_rate_hz(header),
            "ElectricalSeries" if header["label"] in voltage_labels else "TimeSeries",
        )

    enter_stage("opening voltage streams with neuroconv")
    interfaces = voltage_interfaces(edf_path, voltage_labels)

    enter_stage("building NWB file")
    metadata = header_metadata(edf_path)
    for interface, rate_hz in interfaces:
        metadata = dict_deep_update(metadata, interface.get_metadata())
        metadata["Ecephys"]["ElectricalSeries"][interface.metadata_key]["name"] = (
            series_name(rate_hz, single_rate=len(interfaces) == 1)
        )
    metadata["NWBFile"]["data_collection"] = provenance

    nwbfile = make_nwbfile_from_metadata(metadata)
    for interface, _ in interfaces:
        interface.add_to_nwbfile(
            nwbfile, metadata=metadata, data_representation=data_representation
        )

    enter_stage("reading non-voltage signals")
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

    enter_stage(f"writing {nwb_path}")
    if nwb_path.exists():
        nwb_path.unlink()
    configure_and_write_nwbfile(nwbfile, nwbfile_path=nwb_path, backend="hdf5")

    log.info(
        "wrote %s in %.1f s (%s): %d voltage channels in %d ElectricalSeries, %d TimeSeries",
        nwb_path, time.monotonic() - started, memory_usage(),
        len(voltage_labels), len(interfaces), len(other_indices),
    )
    for index in other_indices:
        header = headers[index]
        log.info("  TimeSeries %s [%s]", header["label"], str(header["dimension"]).strip() or "n/a")


def first_edf(input_dir: Path) -> Path:
    """Return the first .edf file in input_dir by name, matching the suffix case-insensitively.

    Raises FileNotFoundError when input_dir holds none.
    """
    edfs = sorted(
        path
        for path in input_dir.iterdir()
        if path.is_file() and path.suffix.lower() == ".edf"
    )
    if not edfs:
        raise FileNotFoundError(f"No EDF file found in INPUT_DIR={input_dir}")
    return edfs[0]


def paths_from_env(env: Mapping[str, str]) -> tuple[Path, Path]:
    """Return the (EDF, NWB) paths the processor convention names.

    The EDF is the first .edf file in INPUT_DIR; the NWB takes its name with an
    .nwb suffix inside OUTPUT_DIR. Raises KeyError when either variable is
    missing and FileNotFoundError when INPUT_DIR holds no EDF.
    """
    for name in ("INPUT_DIR", "OUTPUT_DIR"):
        if not env.get(name):
            raise KeyError(f"{name} must be set")
    edf_path = first_edf(Path(env["INPUT_DIR"]))
    return edf_path, Path(env["OUTPUT_DIR"]) / edf_path.with_suffix(".nwb").name


def log_runtime() -> None:
    """Log the memory and CPUs the container was given and the library versions it runs."""
    limit = memory_limit_bytes()
    log.info(
        "memory limit %s, %s CPUs visible; %s",
        f"{limit / 2**30:.2f} GiB" if limit else "none", os.cpu_count(), memory_usage(),
    )
    log.info(
        "python %s; %s",
        sys.version.split()[0],
        ", ".join(
            f"{package} {importlib.metadata.version(package)}"
            for package in ("neuroconv", "spikeinterface", "neo", "pynwb", "hdmf", "pyedflib")
        ),
    )


def main(argv: list[str]) -> int:
    """Run one conversion from the command line and return an exit code."""
    log_level = os.environ.get("LOG_LEVEL") or "INFO"
    configure_logging(log_level)
    signal.signal(signal.SIGTERM, log_stack_on_sigterm)

    # argparse would otherwise name the module file, which is not how it is run.
    parser = argparse.ArgumentParser(
        prog="python -m edf_nwb.main", description=__doc__
    )
    parser.add_argument(
        "edf", nargs="?", type=Path,
        help="EDF or EDF+ recording to convert; default: the first .edf in INPUT_DIR",
    )
    parser.add_argument(
        "nwb", nargs="?", type=Path,
        help="NWB file to write; default: OUTPUT_DIR/<edf name>.nwb",
    )
    args = parser.parse_args(argv)
    if (args.edf is None) != (args.nwb is None):
        parser.error("give both the EDF and NWB paths, or neither")

    if args.edf is None:
        try:
            edf_path, nwb_path = paths_from_env(os.environ)
        except (KeyError, FileNotFoundError) as error:
            log.error("%s", error.args[0])
            return 2
    else:
        edf_path, nwb_path = args.edf, args.nwb

    # An empty variable counts as unset, matching how the run form leaves a blank field.
    data_representation = os.environ.get("DATA_REPRESENTATION") or "physical_units"
    tz_name = os.environ.get("TZ") or "UTC"
    non_neural_channels = os.environ.get("NON_NEURAL_CHANNELS") or ""

    log.info("converting %s to %s", edf_path, nwb_path)
    log.info(
        "DATA_REPRESENTATION=%s TZ=%s NON_NEURAL_CHANNELS=%s LOG_LEVEL=%s",
        data_representation, tz_name, non_neural_channels, log_level,
    )
    log_runtime()
    if log.isEnabledFor(logging.DEBUG):
        start_heartbeat(HEARTBEAT_SECONDS)

    try:
        convert(
            edf_path,
            nwb_path,
            data_representation=data_representation,
            forced_non_voltage=parse_label_list(non_neural_channels),
            provenance=session_time_provenance(tz_name),
        )
    except Exception:
        log.exception("conversion failed during %s (%s)", _stage, memory_usage())
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
