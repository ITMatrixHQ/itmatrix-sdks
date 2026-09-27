"""Verify staged Python/TypeScript release artifacts and, only with --upload, publish them.

    python scripts/publish.py <dist-dir> [--version X.Y.Z] [--pypi-repository pypi|testpypi] [--upload]

A --pypi-repository testpypi upload publishes Python only; npm has no staging registry.

<dist-dir> holds exactly one wheel, one sdist and one npm tarball plus the SHA256SUMS
that the release gate wrote beside them. Without --upload this is a dry run: every
check below runs, including `twine check --strict` and `npm publish --dry-run`, and
nothing leaves the machine except read-only registry lookups. With --upload the same
checks run first and the exact verified bytes are uploaded; nothing is rebuilt.

Credentials are the caller's: twine reads TWINE_USERNAME=__token__ / TWINE_PASSWORD or
~/.pypirc, npm reads `npm login` or NPM_TOKEN via ~/.npmrc. This script never prints them.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import urllib.error
import urllib.request
import zipfile

PYPI_NAME = "itmatrix"
NPM_NAME = "@itmatrixhq/core"
INDEXES = {
    "pypi": ("https://pypi.org/pypi", "https://upload.pypi.org/legacy/"),
    "testpypi": ("https://test.pypi.org/pypi", "https://test.pypi.org/legacy/"),
}
FORBIDDEN_PARTS = {"benchmarks", "native", "node_modules", ".venv", ".git"}
FORBIDDEN_SUFFIXES = (".so", ".pyd", ".dll", ".dylib", ".o", ".a")


def fail(message):
    sys.exit(f"PUBLISH REFUSED: {message}")


def run(*args, env=None):
    subprocess.run([str(a) for a in args], check=True, env=env)


def twine():
    if subprocess.run([sys.executable, "-c", "import twine"], capture_output=True).returncode == 0:
        return [sys.executable, "-m", "twine"]
    if shutil.which("twine"):
        return ["twine"]
    if shutil.which("uvx"):
        return ["uvx", "twine"]
    fail("twine not found: `pip install twine` in this interpreter, or install uv for `uvx twine`")


def verify_checksums(dist):
    sums = dist / "SHA256SUMS"
    if not sums.is_file():
        fail(f"{sums} missing: stage the artifacts with the checksums the gate recorded")
    listed = {}
    for line in sums.read_text().splitlines():
        if line.strip():
            digest, name = line.split(maxsplit=1)
            listed[name.lstrip("*")] = digest
    for name, digest in listed.items():
        actual = hashlib.sha256((dist / name).read_bytes()).hexdigest()
        if actual != digest:
            fail(f"sha256 mismatch for {name}: SHA256SUMS {digest}, file {actual}")
    return listed


def one(dist, pattern):
    matches = sorted(dist.glob(pattern))
    if len(matches) != 1:
        fail(f"expected exactly one {pattern} in {dist}, found {[m.name for m in matches]}")
    return matches[0]


def members(path):
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as archive:
            return archive.namelist()
    with tarfile.open(path) as archive:
        return archive.getnames()


def inspect(path):
    for name in members(path):
        if FORBIDDEN_PARTS.intersection(Path(name).parts) or name.endswith(FORBIDDEN_SUFFIXES):
            fail(f"nonportable or experimental member in {path.name}: {name}")


def wheel_version(wheel):
    with zipfile.ZipFile(wheel) as archive:
        metadata = next(n for n in archive.namelist() if n.endswith(".dist-info/METADATA"))
        text = archive.read(metadata).decode()
    fields = dict(re.findall(r"^(Name|Version): (.+)$", text, re.M))
    if fields.get("Name") != PYPI_NAME:
        fail(f"wheel is for {fields.get('Name')!r}, not {PYPI_NAME!r}")
    for required in ("License-Expression: MIT", "Project-URL: Source,"):
        if required not in text:
            fail(f"wheel METADATA lacks {required!r}: built from a tree without registry metadata")
    return fields["Version"]


def npm_manifest(tarball):
    with tarfile.open(tarball) as archive:
        manifest = json.load(archive.extractfile("package/package.json"))
    if manifest.get("name") != NPM_NAME:
        fail(f"tarball is for {manifest.get('name')!r}, not {NPM_NAME!r}")
    if manifest.get("private"):
        fail("tarball package.json is private")
    if manifest.get("publishConfig", {}).get("access") != "public":
        fail("tarball lacks publishConfig.access=public; a scoped package would publish restricted")
    if "repository" not in manifest:
        fail("tarball package.json has no repository: built from a tree without registry metadata")
    return manifest


def exists(url):
    try:
        with urllib.request.urlopen(url, timeout=20):
            return True
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return False
        fail(f"registry lookup {url} answered {error.code}")
    except urllib.error.URLError as error:
        fail(f"registry lookup {url} failed: {error.reason}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("dist", type=Path)
    parser.add_argument("--version", help="refuse unless every artifact carries this version")
    parser.add_argument("--pypi-repository", choices=sorted(INDEXES), default="pypi")
    parser.add_argument("--upload", action="store_true", help="publish after every check passes")
    options = parser.parse_args()
    dist = options.dist.resolve()

    listed = verify_checksums(dist)
    wheel = one(dist, f"{PYPI_NAME}-*-py3-none-any.whl")
    sdist = one(dist, f"{PYPI_NAME}-*.tar.gz")
    tarball = one(dist, "itmatrixhq-core-*.tgz")
    for artifact in (wheel, sdist, tarball):
        if artifact.name not in listed:
            fail(f"{artifact.name} is not covered by SHA256SUMS")
        inspect(artifact)

    python_version = wheel_version(wheel)
    manifest = npm_manifest(tarball)
    npm_version = manifest["version"]
    if sdist.name != f"{PYPI_NAME}-{python_version}.tar.gz":
        fail(f"sdist {sdist.name} does not match wheel version {python_version}")
    # PEP 440 and semver spell prereleases differently (0.1.0rc1 / 0.1.0-rc.1).
    if python_version.replace("rc", "-rc.") != npm_version and python_version != npm_version:
        fail(f"Python {python_version} and npm {npm_version} disagree")
    if options.version and options.version not in {python_version, npm_version}:
        fail(f"artifacts are {python_version}/{npm_version}, expected {options.version}")

    lookup, upload_url = INDEXES[options.pypi_repository]
    if exists(f"{lookup}/{PYPI_NAME}/{python_version}/json"):
        fail(f"{PYPI_NAME}=={python_version} is already on {options.pypi_repository}; versions are immutable")
    if exists(f"https://registry.npmjs.org/{NPM_NAME.replace('/', '%2F')}/{npm_version}"):
        fail(f"{NPM_NAME}@{npm_version} is already on npm; versions are immutable")

    twine_command = twine()
    run(*twine_command, "check", "--strict", wheel, sdist)
    run("npm", "publish", tarball, "--dry-run", "--access", "public")

    print(f"\nVerified {PYPI_NAME}=={python_version} ({wheel.name}, {sdist.name}) and "
          f"{NPM_NAME}@{npm_version} ({tarball.name}) from {dist}")
    if not options.upload:
        print("DRY RUN PASSED. Re-run with --upload to publish these exact files.")
        return

    run(*twine_command, "upload", "--non-interactive", "--repository-url", upload_url, wheel, sdist)
    print(f"PUBLISHED {PYPI_NAME}=={python_version} to {options.pypi_repository}")
    if options.pypi_repository == "testpypi":
        # npm has no staging registry; a TestPyPI rehearsal must not publish npm for real.
        print("npm skipped: TestPyPI rehearsal")
        return
    run("npm", "publish", tarball, "--access", "public")
    print(f"PUBLISHED {NPM_NAME}@{npm_version} to npm")


if __name__ == "__main__":
    main()
