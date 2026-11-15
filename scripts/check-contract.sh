#!/usr/bin/env bash
# Opt-in drift check against a configured backend. Read-only unless
# ITM_API_KEY is also set (see the live smoke below).
#
# Three parts:
#   1. the frozen protobuf wire schemas, byte-for-byte, and the served ws
#      schema carries the v2 keyed GEX book messages (GexStrikeBook,
#      GexStrikeBookUpdate) as Frame fields 23 and 24, which is what the SDK
#      decodes at protocol 8;
#   2. the OpenAPI surface the handwritten SDK models — every operation the SDK
#      claims to support must exist, and every query param the SDK sends must be
#      declared. A param the SDK sends that the backend no longer declares is
#      the failure mode this half exists to catch: the request still returns
#      200 while silently doing nothing, which is exactly how `sort` on the
#      dark-pool scanner stayed broken. The stream ticket and connect operations
#      must exist and must not be marked coming-soon;
#   3. only when ITM_API_KEY is set, a live keyed GEX stream smoke (never run
#      by default, never in CI without a key).
set -euo pipefail
cd "$(dirname "$0")/.."
base="${ITM_API_URL:?Set ITM_API_URL to the backend origin to verify}"
artifacts="$(mktemp -d)"
trap 'rm -rf "$artifacts"' EXIT
for wire in rest ws; do
  curl --fail --silent --show-error --max-time 30 "${base%/}/v2/$wire-protocol.proto" -o "$artifacts/$wire.proto"
  cmp "spec/proto/itmatrixhq/$wire/v1/wire.proto" "$artifacts/$wire.proto"
  echo "$wire protobuf schema matches backend byte-for-byte"
done
for message in GexStrikeBook GexStrikeBookUpdate; do
  grep -Eq "^message $message \{" "$artifacts/ws.proto" \
    || { echo "served ws protobuf schema has no $message message" >&2; exit 1; }
done
for field in "GexStrikeBook gex_strike_book = 23;" \
  "GexStrikeBookUpdate gex_strike_book_update = 24;"; do
  grep -Fq "$field" "$artifacts/ws.proto" \
    || { echo "served ws Frame does not declare: $field" >&2; exit 1; }
done
echo "served ws protobuf schema carries GexStrikeBook (23) and GexStrikeBookUpdate (24)"

curl --fail --silent --show-error --max-time 30 "${base%/}/v2/openapi.json" -o "$artifacts/openapi.json"
# The served document is the source of the base URL once it declares servers.
python3 scripts/_base_url.py "$artifacts/openapi.json"

# operationId -> required query params, for the operations the SDK models by
# hand. One row per assertion; add a row whenever a resource method starts
# sending a new param.
python3 - "$artifacts/openapi.json" <<'PY'
import json, sys

spec = json.load(open(sys.argv[1]))

# The params each handwritten resource method sends. A missing one means the
# SDK is sending something the backend has dropped or renamed.
EXPECTED = {
    "offexchange_getActivity": ["from", "to", "tz", "limit", "cursor"],
    "offexchange_getConcentration": ["from", "to", "tz", "limit", "cursor"],
    "offexchange_getProfile": ["from", "to", "tz", "limit", "cursor"],
    "offexchange_getComposition": ["from", "to", "tz", "limit", "cursor"],
    "bars_getBars": ["from", "to", "timeframe", "tz", "adj", "limit", "cursor"],
    "gex_getGrid": ["at", "tz", "expiries", "dte", "top"],
    "fundamentals_getDataset": ["timeframe", "limit", "vintage", "as_of"],
    "darkpool_getLevels": ["from", "to", "tz", "limit", "cursor"],
    "chain_getChain": ["at", "tz", "expiry", "strike_gte", "strike_lte"],
    "gex_getHistory": ["date", "from", "to", "by_expiry", "top"],
    "gex_getReference": ["date", "basis"],
    "flow_getLargeTrades": ["symbol", "session", "min_premium_usd", "limit"],
    "flow_getCrossSection": ["session"],
    "flow_getPremium": [
        "symbol", "sessions", "bucket", "min_premium_usd", "dte", "to_ms",
    ],
    "flow_getEod": [
        "symbol", "session", "from_ms", "to_ms", "min_premium_usd",
        "right", "expiry",
    ],
    # The info methods send no params; they are listed so that the operation
    # disappearing from the spec is caught, which is the other half of drift.
    "ops_getHealthz": [],
    "ops_getReadyz": [],
    "ops_getOpenapi": [],
    # The keyed GEX stream: mint a ticket, then connect.
    "stream_createTicket": [],
    "stream_connectWs": [],
}

# Operations that must be live, not announced. A coming-soon stream means the
# keyed stream the SDK connects to is not served yet.
LIVE = ["stream_createTicket", "stream_connectWs"]

# Params the contract REMOVED. Their reappearance is drift in the other
# direction — the SDK stopped modelling them for a reason.
FORBIDDEN = {}

ops = {}
for path, item in spec.get("paths", {}).items():
    for method, op in item.items():
        if isinstance(op, dict) and "operationId" in op:
            ops[op["operationId"]] = op

failures = []
for op_id, wanted in EXPECTED.items():
    op = ops.get(op_id)
    if op is None:
        failures.append(f"{op_id}: operation is missing from the served spec")
        continue
    declared = {p.get("name") for p in op.get("parameters", [])}
    for name in wanted:
        if name not in declared:
            failures.append(f"{op_id}: SDK sends `{name}`, backend does not declare it")

for op_id, banned in FORBIDDEN.items():
    op = ops.get(op_id)
    if op is None:
        continue
    declared = {p.get("name") for p in op.get("parameters", [])}
    for name in banned:
        if name in declared:
            failures.append(f"{op_id}: `{name}` was removed from the contract but is declared again")

for op_id in LIVE:
    availability = (ops.get(op_id) or {}).get("x-availability")
    if availability == "coming-soon":
        failures.append(f"{op_id}: marked x-availability coming-soon in the served spec")

# Every operation the SDK advertises must exist. Scope the lightweight source
# parser to SUPPORTED_OPERATIONS so the adjacent, deliberately absent legacy
# inventory cannot be mistaken for callable surface.
in_supported = False
for line in open("ts/packages/core/src/resources.ts"):
    line = line.strip()
    if line.startswith("export const SUPPORTED_OPERATIONS"):
        in_supported = True
        continue
    if in_supported and line.startswith("]"):
        break
    if in_supported and line.startswith('"') and line.endswith('",') and "_" in line:
        op_id = line[1:-2]
        if op_id.split("_")[0].isalpha() and op_id not in ops and "_" in op_id:
            failures.append(f"{op_id}: listed in SUPPORTED_OPERATIONS, absent from the served spec")

if failures:
    print("contract drift:", file=sys.stderr)
    for f in failures:
        print(f"  - {f}", file=sys.stderr)
    raise SystemExit(1)
print(f"OpenAPI surface matches the SDK models ({len(ops)} operations checked)")
PY

# Live keyed GEX stream smoke. Opt in by exporting ITM_API_KEY (an itm_ key on
# an account that is on the stream preview list). Otherwise this part is skipped.
# It mints a ticket, connects with the SDK in this tree, subscribes to SPY GEX,
# needs a GexStrikeBook keyframe within 10 seconds (the SDK checks its CRC before it
# yields) and then applies one more frame. Needs the Python package's
# dependencies, for example: pip install -e "python[stream]".
if [ -z "${ITM_API_KEY:-}" ]; then
  echo "live stream smoke skipped: ITM_API_KEY is not set"
  exit 0
fi
PYTHONPATH="python/src${PYTHONPATH:+:$PYTHONPATH}" python3 - "${base%/}" <<'PY'
import asyncio
import os
import sys

import itmatrix as itm


async def main(base_url: str) -> None:
    async with itm.AsyncITMClient(
        api_key=os.environ["ITM_API_KEY"], base_url=base_url
    ) as client:
        spy = client.stream.gex("SPY")
        keyframe = await spy.ready(10)
        assert keyframe.meta.reason is not None, "first book was not a keyframe"
        assert len(keyframe) > 0, "keyframe has no strikes"
        print(
            f"keyframe ok: version {keyframe.meta.version}, "
            f"{len(keyframe)} strikes, reason {keyframe.meta.reason}, age {keyframe.age:.1f}s"
        )
        # Updates arrive about every 5 s while the market is open. The
        # window allows a slow engine tick or a keyframe-sized change.
        following = await spy.changed(30)
        kind = "update" if following.meta.reason is None else "keyframe"
        print(f"{kind} ok: version {following.meta.version}, {len(following.meta.changed)} changed")
        await spy.aclose()


try:
    asyncio.run(main(sys.argv[1]))
except itm.StreamError as error:
    raise SystemExit(f"live stream smoke failed: {error.code} {error.reason or ''}: {error.message}")
except TimeoutError:
    raise SystemExit("live stream smoke failed: no frame in time")
PY
echo "live stream smoke passed"
