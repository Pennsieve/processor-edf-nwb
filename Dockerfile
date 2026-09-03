FROM ghcr.io/catalystneuro/neuroconv:v0.9.3

# EDF stores the recording's local wall clock with no timezone, so pynwb stamps
# whatever the container's local zone is. Fix that to UTC so the same EDF always
# produces the same session_start_time: the header digits are preserved verbatim
# and any later correction is a uniform, known shift. Deployments that know where
# the recording was made should override with an IANA zone name (which is
# DST-aware), e.g. TZ=America/New_York.
ENV TZ=UTC

# pynwb writes its type-map cache under the user cache directory (~/.cache) the
# first time it is imported. The container may run as a non-root user whose HOME
# is not writable, so point HOME at a scratch path; pynwb creates it on demand.
ENV HOME=/tmp/edf-nwb

WORKDIR /app

COPY convert_edf.py /app/convert_edf.py

CMD ["python", "/app/convert_edf.py"]
