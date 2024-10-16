FROM ghcr.io/catalystneuro/neuroconv:v0.6.1

RUN apt-get update && apt-get install -y gettext

WORKDIR /app

COPY neuroconv_edf.template.yml /app/neuroconv_edf.template.yml

COPY --chmod=755 entrypoint.sh /app/entrypoint.sh

ENTRYPOINT ["/app/entrypoint.sh"]
