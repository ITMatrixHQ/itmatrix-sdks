#!/usr/bin/env bash
# Regenerate the two protobuf bindings (`_wire`) the client uses.
set -euo pipefail
cd "$(dirname "$0")/../python"

if [ ! -d .venv ]; then
  python3 -m venv .venv 2>/dev/null || python3 -m venv --without-pip .venv
fi
if ! .venv/bin/pip --version >/dev/null 2>&1; then
  curl -sS https://bootstrap.pypa.io/get-pip.py | .venv/bin/python
fi
.venv/bin/pip install -q -r requirements-gen.txt
.venv/bin/pip install -q -e .

generated_dir="$(mktemp -d)"
trap 'rm -rf "$generated_dir"' EXIT
.venv/bin/python -m grpc_tools.protoc \
  -I../spec/proto \
  --python_out="$generated_dir" \
  ../spec/proto/itmatrixhq/ws/v1/wire.proto \
  ../spec/proto/itmatrixhq/rest/v1/wire.proto

mkdir -p src/itmatrix/_core/_wire
cp "$generated_dir/itmatrixhq/rest/v1/wire_pb2.py" src/itmatrix/_core/_wire/rest_pb2.py
cp "$generated_dir/itmatrixhq/ws/v1/wire_pb2.py" src/itmatrix/_core/_wire/ws_pb2.py
.venv/bin/ruff format src/itmatrix/_core/_wire

echo "python generated: 2 protobuf bindings"
