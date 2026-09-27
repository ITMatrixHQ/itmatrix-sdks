"""Clients work without a context manager, and an unclosed one never hangs exit."""

import gc
import subprocess
import sys
import textwrap
import threading
import unittest
import warnings
from pathlib import Path

import httpx

import itmatrix as itm

SRC = str(Path(__file__).resolve().parent.parent / "src")
GRID = {"data": {"strikes": [], "net_gex": 1.0, "max_abs_gex": 1.0}}

# The child builds a sync client, makes an offline call, and never closes it.
# Its transport reports when the pool is closed, which only the finalizer can do.
CHILD = textwrap.dedent(
    """
    import sys
    sys.path.insert(0, {src!r})
    import httpx
    import itmatrix as itm

    class Transport(httpx.MockTransport):
        async def aclose(self):
            print("pool-closed", flush=True)

    transport = Transport(lambda request: httpx.Response(200, json={grid!r}))
    client = itm.ITMClient(api_key="offline", http_transport=transport)
    print("net", client.get_gex("SPY").data.net_gex, flush=True)
    {tail}
    """
)


def _loop_threads() -> set[int]:
    return {
        t.ident for t in threading.enumerate() if t.name == "itmatrix-client-loop"
    }


def _run_child(tail: str) -> subprocess.CompletedProcess[str]:
    code = CHILD.format(src=SRC, grid=GRID, tail=tail)
    return subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


class SyncLifecycleTests(unittest.TestCase):
    def test_unclosed_client_lets_the_interpreter_exit_and_closes_its_pool(self):
        child = _run_child("# never closed; the interpreter just exits")
        self.assertEqual(child.returncode, 0, child.stderr)
        self.assertEqual(child.stdout.split(), ["net", "1.0", "pool-closed"])

    def test_dropped_client_is_closed_when_collected(self):
        child = _run_child('del client\nprint("after-del", flush=True)')
        self.assertEqual(child.returncode, 0, child.stderr)
        self.assertEqual(
            child.stdout.split(), ["net", "1.0", "pool-closed", "after-del"]
        )

    def test_plain_client_close_is_idempotent_and_use_after_close_raises(self):
        before = _loop_threads()
        client = itm.ITMClient(
            http_transport=httpx.MockTransport(lambda _: httpx.Response(200, json=GRID))
        )
        self.assertEqual(client.get_gex("SPY").data.net_gex, 1.0)
        self.assertEqual(client.flow.__class__.__name__, "_BlockingResource")
        self.assertFalse(client.closed)
        client.close()
        client.close()
        self.assertTrue(client.closed)
        self.assertEqual(_loop_threads(), before)
        with self.assertRaisesRegex(RuntimeError, "client is closed"):
            client.get_gex("SPY")
        with self.assertRaisesRegex(RuntimeError, "client is closed"):
            client.info.health()
        with self.assertRaisesRegex(RuntimeError, "client is closed"), client:
            pass

    def test_dropping_an_unclosed_client_stops_its_loop_thread(self):
        before = _loop_threads()
        client = itm.ITMClient(
            http_transport=httpx.MockTransport(lambda _: httpx.Response(200, json=GRID))
        )
        client.get_gex("SPY")
        self.assertEqual(len(_loop_threads() - before), 1)
        del client
        gc.collect()
        self.assertEqual(_loop_threads(), before)

    def test_a_held_resource_keeps_its_client_open(self):
        flow = itm.ITMClient(
            http_transport=httpx.MockTransport(
                lambda _: httpx.Response(200, json={"data": {"ok": True}})
            )
        ).info
        gc.collect()
        self.assertEqual(flow.health().data, {"ok": True})
        del flow
        gc.collect()


class AsyncLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_plain_async_client_closes_with_aclose(self):
        client = itm.AsyncITMClient(
            http_transport=httpx.MockTransport(lambda _: httpx.Response(200, json=GRID))
        )
        self.assertEqual((await client.get_gex("SPY")).data.net_gex, 1.0)
        await client.aclose()
        await client.aclose()
        with self.assertRaisesRegex(RuntimeError, "client is closed"):
            await client.get_gex("SPY")
        with self.assertRaisesRegex(RuntimeError, "client is closed"):
            async with client:
                pass

    async def test_unclosed_async_client_warns_when_collected(self):
        client = itm.AsyncITMClient(
            http_transport=httpx.MockTransport(lambda _: httpx.Response(200, json=GRID))
        )
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            del client
            gc.collect()
        self.assertEqual(
            [w.category for w in caught if "aclose" in str(w.message)],
            [ResourceWarning],
        )

    async def test_closed_or_caller_owned_async_clients_do_not_warn(self):
        http = httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _: httpx.Response(200, json=GRID))
        )
        self.addAsyncCleanup(http.aclose)
        closed = itm.AsyncITMClient(
            http_transport=httpx.MockTransport(lambda _: httpx.Response(200, json=GRID))
        )
        await closed.aclose()
        borrowed = itm.AsyncITMClient(http_client=http)
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            del closed, borrowed
            gc.collect()
        self.assertEqual([w for w in caught if w.category is ResourceWarning], [])


if __name__ == "__main__":
    unittest.main()
