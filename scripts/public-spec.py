"""Reduce a served OpenAPI document to what this public repository may pin.

The pin keeps public and app-only operations plus the three infra routes every
client calls (/healthz, /readyz, /v2/openapi.json). It drops other internal
operations, component schemas only those operations use, any media type other
than JSON, protobuf and plain text, and sentences naming a forbidden token.

    python3 scripts/public-spec.py <served.json> <pinned.json>
"""
import json
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _markers import find_marker  # noqa: E402

INFRA = {"/healthz", "/readyz", "/v2/openapi.json"}
PUBLIC_MEDIA = {"application/json", "application/x-protobuf", "text/plain"}
PRIVATE_TEXT = re.compile(r"/admin\b", re.I)
SENTENCE = re.compile(r"(?<=[.;])\s+|\n\n")


PHRASES = []


def private(text):
    return bool(PRIVATE_TEXT.search(text) or find_marker(text))


def scrub(text):
    for pattern, replacement in PHRASES:
        text = pattern.sub(replacement, text)
    if not private(text):
        return text
    kept = [part for part in SENTENCE.split(text) if not private(part)]
    return " ".join(kept).strip()


def clean(node):
    if isinstance(node, dict):
        content = node.get("content")
        if isinstance(content, dict):
            for media in [m for m in content if m not in PUBLIC_MEDIA]:
                del content[media]
        for key, value in list(node.items()):
            if isinstance(value, str) and key in {"description", "summary"}:
                node[key] = scrub(value)
            else:
                clean(value)
    elif isinstance(node, list):
        for value in node:
            clean(value)


def refs(node, found):
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/components/schemas/"):
            found.add(ref.rsplit("/", 1)[1])
        for value in node.values():
            refs(value, found)
    elif isinstance(node, list):
        for value in node:
            refs(value, found)


def reachable(roots, schemas):
    live, frontier = set(), set()
    refs(roots, frontier)
    while frontier:
        name = frontier.pop()
        if name in live or name not in schemas:
            continue
        live.add(name)
        refs(schemas[name], frontier)
    return live


def main(source, destination):
    spec = json.load(open(source))
    schemas = spec.get("components", {}).get("schemas", {})
    removed = []
    for path in list(spec["paths"]):
        item = spec["paths"][path]
        for method in list(item):
            operation = item[method]
            if isinstance(operation, dict) and operation.get("x-exposure") == "internal" and path not in INFRA:
                removed.append(item.pop(method))
        if not any(isinstance(op, dict) and "operationId" in op for op in item.values()):
            del spec["paths"][path]
    # Drop schemas only the removed operations reach; unreferenced public schemas stay.
    kept_schemas = reachable(spec["paths"], schemas)
    for name in reachable(removed, schemas) - kept_schemas:
        del schemas[name]
    # An unreferenced schema that describes private material has no public reader.
    for name in [n for n in schemas if n not in kept_schemas and private(json.dumps(schemas[n]))]:
        del schemas[name]
    clean(spec)
    text = json.dumps(spec, indent=2, ensure_ascii=False) + "\n"
    if private(text):
        raise SystemExit("public-spec: a forbidden reference survived the filter")
    open(destination, "w").write(text)
    kept = sum(1 for item in spec["paths"].values() for op in item.values() if isinstance(op, dict))
    print(f"public pin: {kept} operations, {len(schemas)} schemas")


if __name__ == "__main__":
    main(*sys.argv[1:3])
