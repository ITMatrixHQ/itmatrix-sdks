"""One default base URL per language, matching the contract's declared server.

Each package defines its default API origin exactly once:
`python/src/itmatrix/_core/config.py` and `ts/packages/core/src/config.ts`. This
check fails if either is missing, if the literal appears anywhere else in that
package's source, if the two disagree, or, once an OpenAPI document declares
`servers`, if they differ from `servers[0].url`. A document without `servers`
is reported as skipped, never as a match.

    python3 scripts/_base_url.py [openapi.json]   # check (default: the pin)
    python3 scripts/_base_url.py --default        # print the default origin
"""
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parent.parent
SOURCES = {
    "python": (
        ROOT / "python/src/itmatrix/_core/config.py",
        re.compile(r'^DEFAULT_BASE_URL = "([^"]+)"$', re.M),
        ROOT / "python/src",
        "*.py",
    ),
    "typescript": (
        ROOT / "ts/packages/core/src/config.ts",
        re.compile(r'^export const DEFAULT_BASE_URL = "([^"]+)";$', re.M),
        ROOT / "ts/packages/core/src",
        "*.ts",
    ),
}


def defaults():
    """{language: default origin}, or raise SystemExit naming what is wrong."""
    found, failures = {}, []
    for language, (config, pattern, tree, glob) in SOURCES.items():
        matches = pattern.findall(config.read_text()) if config.is_file() else []
        if len(matches) != 1:
            failures.append(f"{language}: expected one DEFAULT_BASE_URL in {config.relative_to(ROOT)}")
            continue
        value = matches[0]
        for path in sorted(tree.rglob(glob)):
            if path != config and value in path.read_text(errors="replace"):
                failures.append(
                    f"{language}: {path.relative_to(ROOT)} repeats {value}; use DEFAULT_BASE_URL"
                )
        found[language] = value
    if len(set(found.values())) > 1:
        failures.append(f"languages disagree on the default base URL: {found}")
    if failures:
        raise SystemExit("Base URL check failed:\n" + "\n".join(failures))
    return found


def check(spec_path):
    """Compare every default with the document's first server; return a status line."""
    found = defaults()
    path = Path(spec_path).resolve()
    shown = path.relative_to(ROOT) if path.is_relative_to(ROOT) else path.name
    servers = json.loads(path.read_text()).get("servers") or []
    if not servers:
        return f"Base URL check skipped: {shown} declares no servers (defaults {sorted(set(found.values()))})"
    declared = str(servers[0].get("url", "")).rstrip("/")
    wrong = {lang: value for lang, value in found.items() if value.rstrip("/") != declared}
    if wrong:
        raise SystemExit(f"Base URL check failed: servers[0].url is {declared}, defaults are {wrong}")
    return f"Base URL check passed: every default equals servers[0].url {declared}"


if __name__ == "__main__":
    if sys.argv[1:] == ["--default"]:
        print(defaults()["python"])
    else:
        print(check(sys.argv[1] if len(sys.argv) > 1 else ROOT / "spec" / "openapi.json"))
