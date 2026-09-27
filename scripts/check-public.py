"""Check the committed/public source boundary, independent of package builds."""
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _base_url import check as check_base_url  # noqa: E402
from _markers import find_marker  # noqa: E402

root = Path(__file__).resolve().parent.parent
paths = subprocess.check_output(
    ["git", "ls-files", "--cached", "--others", "--exclude-standard"], cwd=root, text=True
).splitlines()
failures = []
for name in paths:
    path = root / name
    if not path.is_file():
        continue
    if name.startswith("skills/internal/") or any(part in {"node_modules", ".venv", "target"} for part in path.relative_to(root).parts):
        failures.append(name)
    if name.endswith((".py", ".ts", ".proto", ".md", ".json", ".sh", ".yml", ".yaml", ".toml")):
        marker = find_marker(path.read_text(errors="replace"))
        if marker:
            failures.append(f"{name}: forbidden token {marker}")
for forbidden in ("python/src/itmatrix/api", "python/src/itmatrix/_core/api", "ts/packages/core/src/generated"):
    if (root / forbidden).exists():
        failures.append(forbidden + ": generated endpoint forest")
spec = (root / "spec" / "openapi.json").read_text()
for marker in ('"/admin/', '"/v2/admin/'):
    if marker in spec:
        failures.append(f"spec/openapi.json: internal route {marker} (refresh through scripts/fetch-spec.sh)")
if failures:
    raise SystemExit("Public boundary failed:\n" + "\n".join(failures))
print(check_base_url(root / "spec" / "openapi.json"))
print("Public source boundary passed")
