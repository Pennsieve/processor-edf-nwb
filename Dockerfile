# neuroconv 0.10.2 (built 2026-09-10). Pinned by digest because the
# registry's version tags are not fixed. Neuroconv's release workflow derives
# the tag from the last entry of a lexicographically sorted `git tag --list`,
# so every release since 0.9.3 has been pushed as both `latest` and `v0.9.3`
# and no v0.10.x tags exist. To upgrade, resolve the digest of the current
# `latest` and confirm the neuroconv version inside it before changing this.
# EDF stream support (multi-rate files) needs neuroconv >= 0.10.0.
FROM ghcr.io/catalystneuro/neuroconv@sha256:01b9bc987d366a539c75019cd63da77c718b75235d380a5c51868e7f0e433c4d

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

COPY edf_nwb/ /app/edf_nwb

# The package is imported by name rather than run as a path, so /app has to be
# importable whatever directory the container is started in.
ENV PYTHONPATH=/app

CMD ["python", "-m", "edf_nwb.main"]
