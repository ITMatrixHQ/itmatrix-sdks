# Release

Python publishes as `itmatrix` and TypeScript as `@itmatrixhq/core`; versions move
together. Per-release detail lives in [CHANGELOG.md](../CHANGELOG.md). The pinned
contract in `spec/` is the public subset of the served OpenAPI document plus the public
REST and WebSocket protobuf schemas; `scripts/fetch-spec.sh` refreshes it and
`scripts/check-contract.sh` compares the SDK with a live deployment.

| Package | Runtime baseline | Direct runtime dependencies |
| --- | --- | --- |
| Python `itmatrix` | Python 3.10+ | httpx, anyio, protobuf, typing-extensions, tzdata |
| TypeScript `@itmatrixhq/core` | Node 22+ or a modern browser with fetch/WebSocket | @bufbuild/protobuf |

Counts exclude transitive dependencies and development/build tools. Python's
`itmatrix[stream]` extra adds websockets; REST imports and sync/async clients work
without it. anyio runs the synchronous client's async portal; typing-extensions
preserves Python 3.10 support. tzdata supplies the IANA zones where the OS has none
(Windows, slim containers); `zoneinfo` prefers the system database when present. Protobuf remains a base dependency because public
models and low-level wire APIs use it. TypeScript uses built-in fetch/WebSocket.

Python builds a `py3-none-any` wheel: no SDK-specific native binary or compiler is
needed. The standard protobuf runtime can use its bundled native accelerator or its
pure-Python implementation; the latter is tested explicitly.

## Publishing

Python publishes as `itmatrix` on PyPI and TypeScript as `@itmatrixhq/core` on npm.
Registry versions are immutable: a published version can be
yanked or deprecated, never replaced, so only gated bytes are uploaded.

**One-time owner setup** (none of this can be done by the scripts):

1. Public GitHub repository `ITMatrixHQ/itmatrix-sdks`, pushed from a fresh-history tree
   (package metadata links to it; until it exists those links 404 on PyPI and npm).
2. PyPI account with 2FA. For the first upload, an account-scoped API token; after it,
   replace that with a project-scoped token or, better, a trusted publisher: owner
   `ITMatrixHQ`, repository `itmatrix-sdks`, workflow `publish.yml`, environment `pypi`.
   PyPI also accepts that trusted publisher as a *pending* publisher before the project
   exists, which makes the manual PyPI step unnecessary.
3. npm account with 2FA and an npm organization named `itmatrixhq` (the scope). The first
   `@itmatrixhq/core` publish is manual with `npm login` or a granular token. Afterwards,
   in the package's settings, add the trusted publisher (same repository, workflow
   `publish.yml`, environment `npm`) and set publishing access to require 2FA and
   disallow tokens.
4. GitHub environments `pypi` and `npm` on the repository, with required reviewers.

**First release, from the exact staged bytes.** Stage one wheel, one sdist and one npm
tarball from the gated tag with their checksums (`sha256sum * > SHA256SUMS`), then:

```sh
python scripts/publish.py <staged-dir> --version X.Y.Z                 # dry run: every check, no upload
python scripts/publish.py <staged-dir> --version X.Y.Z --pypi-repository testpypi --upload  # optional rehearsal, Python only
python scripts/publish.py <staged-dir> --version X.Y.Z --upload        # publish
```

The dry run verifies SHA256SUMS, that the wheel is universal and the three versions agree,
that registry metadata (MIT license expression, source URL, `publishConfig.access=public`)
is in the built files, that no native or experimental member slipped in, and that neither
version is already on its registry; then it runs `twine check --strict` and
`npm publish --dry-run`. The script needs twine (`pip install twine`, or `uvx`).

**Later releases** publish the same way, by hand. `.github/workflows/publish.yml` (manual
trigger only) checks a tag against both manifests, runs the Python and TypeScript gates and
uploads through OIDC trusted publishing; it stays unused until trusted publishers exist.

## Checks and artifacts

From the repository root run `python scripts/check.py all`, or `python` / `ts` for one
package. `scripts/check.sh` is a convenience wrapper. The checks lint, test and build
each package, inspect the actual release archives for native or experimental members,
assert the universal wheel tag, install the wheel with base dependencies in a clean
environment (tests run under both protobuf runtimes), and import the packed npm tarball
in a clean consumer.

Artifacts land in `python/dist/` and `ts/`. Release tooling selects the exact version,
not files left by earlier builds.

The CI workflow runs both packages on Linux, macOS and Windows, plus Python 3.10, 3.11,
3.13 and 3.14 on Linux (the main matrix uses 3.12). It is manually triggered only; nothing
runs on push or pull requests.
