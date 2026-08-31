FROM ghcr.io/catalystneuro/neuroconv:v0.9.3

RUN apt-get update && apt-get install -y gettext

# EDF stores the recording's local wall clock with no timezone, so pynwb stamps
# whatever the container's local zone is. Fix that to UTC so the same EDF always
# produces the same session_start_time: the header digits are preserved verbatim
# and any later correction is a uniform, known shift. Deployments that know where
# the recording was made should override with an IANA zone name (which is
# DST-aware), e.g. TZ=America/New_York.
ENV TZ=UTC

WORKDIR /app

COPY neuroconv_edf.template.yml /app/neuroconv_edf.template.yml

COPY --chmod=755 entrypoint.sh /app/entrypoint.sh

ENTRYPOINT ["/app/entrypoint.sh"]
