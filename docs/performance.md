# Python native acceleration

The SDK uses the standard protobuf runtime. On the verified development installation its active engine is `upb`, the native C extension; the SDK is not decoding protobuf in a Python byte loop.

A 2026-09-05 microbenchmark on a development machine used one actual SPY GEX protobuf response (183 rows, 5,517 bytes), retained locally for follow-up measurements (not distributed). Median of five runs of 1,000 iterations, excluding network and client construction:

| Operation | Time per response |
| --- | ---: |
| Native protobuf decode | 9.28 microseconds |
| Python domain model conversion of an already decoded message | 462.61 microseconds |
| Decode and model conversion together | 484.85 microseconds |

This is one workload on one development machine, not a throughput claim for large chains, replay or streams. No custom FFI layer was added during that initial assessment. Profile representative batch sizes before adding native code. An optional Rust/PyO3 module would make sense for measured bulk conversion/columnar output bottlenecks; wrapping individual Python object allocations with FFI is not automatically faster.

Shipping native code requires wheels for supported OS/CPU combinations. An ABI-stable Python extension can reduce the Python-version matrix, but does not eliminate platform-specific binaries. Keep a Python fallback for uncovered machines, and avoid requiring users to install a compiler for the ordinary SDK path.

Sources: [protobuf Python runtime implementations](https://github.com/protocolbuffers/protobuf/blob/main/python/README.md), [Maturin packaging](https://www.maturin.rs/).

## Follow-up: faster eager Python models

The default SDK now reduces full protobuf-to-dataclass time about **44%** in
paired runs: 529 → 297 µs, 491 → 276 µs, and 731 → 412 µs under differing
shared-host load. This uses cached slot setters, one bound presence check per
row, and a bounded expiry-string cache. The public frozen dataclass API is
unchanged; no custom native extension is required. All 20 Python tests, lint
and package builds passed.
