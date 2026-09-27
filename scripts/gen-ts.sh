#!/usr/bin/env bash
# Regenerate the two protobuf bindings (`_wire`) the client uses.
set -euo pipefail
cd "$(dirname "$0")/../ts"

protoc_runner="../python/.venv/bin/python"
if [ ! -x "$protoc_runner" ] || ! "$protoc_runner" -c 'import grpc_tools.protoc' 2>/dev/null; then
  echo "python generator environment missing; run scripts/gen-python.sh first" >&2
  exit 1
fi

generated_dir="$(mktemp -d)"
trap 'rm -rf "$generated_dir"' EXIT
"$protoc_runner" -m grpc_tools.protoc \
  -I../spec/proto \
  --plugin=protoc-gen-ts_proto="$(pwd)/node_modules/.bin/protoc-gen-ts_proto" \
  --ts_proto_out="$generated_dir" \
  --ts_proto_opt=esModuleInterop=true,forceLong=bigint,useOptionals=messages,useExactTypes=false,outputJsonMethods=false,outputClientImpl=false \
  ../spec/proto/itmatrixhq/ws/v1/wire.proto \
  ../spec/proto/itmatrixhq/rest/v1/wire.proto

mkdir -p packages/core/src/_wire
cp "$generated_dir/itmatrixhq/rest/v1/wire.ts" packages/core/src/_wire/rest.ts
cp "$generated_dir/itmatrixhq/ws/v1/wire.ts" packages/core/src/_wire/ws.ts

echo "ts generated: 2 protobuf bindings"
