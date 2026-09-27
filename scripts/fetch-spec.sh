#!/usr/bin/env bash
# Refresh the pinned public contract artifacts. Run only when the contract moves.
set -euo pipefail
cd "$(dirname "$0")/.."

download_dir="$(mktemp -d)"
trap 'rm -rf "$download_dir"' EXIT
base="${ITM_API_URL:-$(python3 scripts/_base_url.py --default)}"
base="${base%/}"
curl -fsS "$base/v2/openapi.json" -o "$download_dir/openapi.json"
curl -fsS "$base/v2/ws-protocol.proto" -o "$download_dir/ws-wire.proto"
curl -fsS "$base/v2/rest-protocol.proto" -o "$download_dir/rest-wire.proto"

python3 scripts/public-spec.py "$download_dir/openapi.json" spec/openapi.json
install -m 0644 "$download_dir/ws-wire.proto" spec/proto/itmatrixhq/ws/v1/wire.proto
install -m 0644 "$download_dir/rest-wire.proto" spec/proto/itmatrixhq/rest/v1/wire.proto

# Only the OpenAPI document and the two protobuf schemas are pinned here.
echo "public pins refreshed: OpenAPI + REST/WS protobuf schemas"
