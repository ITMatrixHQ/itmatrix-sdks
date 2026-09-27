"""Build and inspect release candidates on Linux, macOS and Windows."""
import argparse
import http.server
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
import zipfile

ROOT = Path(__file__).resolve().parent.parent


def run(*args, cwd=ROOT, env=None, timeout=None):
    command = [str(a) for a in args]
    command[0] = shutil.which(command[0]) or command[0]
    subprocess.run(command, cwd=cwd, env=env, check=True, timeout=timeout)


class _Grid(http.server.BaseHTTPRequestHandler):
    """A keep-alive local API, so an idle pooled socket is part of the smoke."""

    protocol_version = "HTTP/1.1"
    body = b'{"data":{"spot":600,"net_gex":1,"max_abs_gex":1,"strikes":[]}}'

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(self.body)))
        self.end_headers()
        self.wfile.write(self.body)

    def log_message(self, *_args):
        pass


def inspect(path):
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
    else:
        with tarfile.open(path) as archive:
            names = archive.getnames()
    forbidden = {"benchmarks", "native", "node_modules", ".venv", ".git"}
    for name in names:
        parts = Path(name).parts
        if forbidden.intersection(parts) or name.endswith((".so", ".pyd", ".dll", ".dylib", ".o", ".a")):
            raise RuntimeError(f"Nonportable or experimental release member: {path}: {name}")
    print(f"Release archive passed: {path.name} ({len(names)} entries)", flush=True)


def python():
    directory = ROOT / "python"
    run(sys.executable, "-m", "venv", ".venv", cwd=directory)
    interpreter = directory / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    run(interpreter, "-m", "pip", "install", "-q", "-e", ".[stream]", "build", "hatchling", "ruff", cwd=directory)
    run(interpreter, "-m", "ruff", "check", "src", "tests", "--exclude", "*_pb2.py", cwd=directory)
    run(interpreter, "-m", "unittest", "discover", "-s", "tests", "-v", cwd=directory)
    # Prove protobuf's portable implementation works without its C accelerator.
    run(interpreter, "-m", "unittest", "discover", "-s", "tests", cwd=directory,
        env={**os.environ, "PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION": "python"})
    with tempfile.TemporaryDirectory(dir=directory) as output:
        run(interpreter, "-m", "build", "--outdir", output, cwd=directory)
        archives = list(Path(output).iterdir())
        for archive in archives:
            inspect(archive)
        wheel = next(p for p in archives if p.suffix == ".whl")
        assert wheel.name.endswith("-py3-none-any.whl"), wheel
        # Install the actual wheel outside the source tree, with base dependencies only.
        with tempfile.TemporaryDirectory() as smoke:
            run(sys.executable, "-m", "venv", smoke)
            executable = Path(smoke) / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
            run(executable, "-m", "pip", "install", "-q", wheel, cwd=smoke)
            run(executable, "-I", "-c", "import importlib.util; import itmatrix as itm; "
                "assert importlib.util.find_spec('websockets') is None; "
                "client = itm.ITMClient(api_key='offline-smoke'); client.close()", cwd=smoke)
        destination = directory / "dist"
        destination.mkdir(exist_ok=True)
        for archive in archives:
            shutil.copy2(archive, destination / archive.name)


def ts():
    directory = ROOT / "ts"
    run("npm", "ci", cwd=directory)
    run("npm", "run", "typecheck", cwd=directory)
    run("npm", "run", "build", "-ws", cwd=directory)
    run("npm", "test", cwd=directory)
    run("npm", "pack", "-w", "@itmatrixhq/core", cwd=directory)
    for archive in directory.glob("itmatrixhq-core-*.tgz"):
        inspect(archive)
        with tempfile.TemporaryDirectory() as smoke:
            run("npm", "install", "--ignore-scripts", "--omit=dev", archive, cwd=smoke)
            # A REST client that is never closed must not keep Node running.
            server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Grid)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            try:
                run("node", "--input-type=module", "-e",
                    "import { ITMClient } from '@itmatrixhq/core';"
                    "const client = new ITMClient({token:'offline-smoke', baseUrl: process.argv[1]});"
                    "const { data } = await client.getGex('SPY');"
                    "if (data.net_gex !== 1) throw new Error('unexpected grid');",
                    f"http://127.0.0.1:{server.server_port}", cwd=smoke, timeout=60)
            finally:
                server.shutdown()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("package", nargs="?", default="all", choices=["all", "python", "ts"])
    package = parser.parse_args().package
    run(sys.executable, "scripts/check-public.py")
    for name, check in [("python", python), ("ts", ts)]:
        if package in {"all", name}:
            check()
    print(f"SDK CHECKS PASSED: {package}")
