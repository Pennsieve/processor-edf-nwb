FROM ghcr.io/catalystneuro/neuroconv:latest

WORKDIR /run

COPY neuroconv_edf.yml /run/neuroconv_edf.yml

CMD ["neuroconv", "neuroconv_edf.yml", "--overwrite", "--data-folder-path", "/data/input/", "--output-folder-path", "/data/output/"]
