#!/bin/sh

rm -rf build
mkdir -p build
curl -L https://github.com/grafana/loki/releases/download/v2.5.0/logcli-linux-amd64.zip -o build/logcli-linux-amd64.zip

pushd build
unzip logcli-linux-amd64.zip
popd

podman build -t logcli .
