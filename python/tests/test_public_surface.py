"""The package says nothing about operations or transports an API key cannot use."""

import sys
import unittest
from pathlib import Path

import itmatrix
from itmatrix._core._wire import rest_pb2, ws_pb2

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"


class PublicSurfaceTests(unittest.TestCase):
    def setUp(self) -> None:
        if not (SCRIPTS / "_leaks.py").exists():
            self.skipTest("the repository scripts are not in this tree")
        sys.path.insert(0, str(SCRIPTS))
        self.addCleanup(sys.path.remove, str(SCRIPTS))
        import _leaks

        self.leaks = _leaks

    def test_the_bindings_define_only_the_public_messages(self) -> None:
        ws = set(ws_pb2.DESCRIPTOR.message_types_by_name) | set(
            ws_pb2.DESCRIPTOR.enum_types_by_name
        )
        rest = set(rest_pb2.DESCRIPTOR.message_types_by_name) | set(
            rest_pb2.DESCRIPTOR.enum_types_by_name
        )
        self.assertEqual(ws - self.leaks.WS_MESSAGES, set())
        self.assertEqual(rest - self.leaks.REST_MESSAGES, set())

    def test_no_source_file_names_what_a_key_cannot_use(self) -> None:
        package = Path(itmatrix.__file__).resolve().parent
        found = {
            str(path.relative_to(package)): leak
            for path in package.rglob("*.py")
            if (leak := self.leaks.find_leak(path.read_text())) is not None
        }
        self.assertEqual(found, {})


if __name__ == "__main__":
    unittest.main()
