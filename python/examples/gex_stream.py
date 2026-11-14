"""Stream a live GEX book for one symbol.

Needs an ITMatrixHQ API key and the stream extra:

    pip install -U "itmatrix[stream]"
    ITM_API_KEY=itm_... python gex_stream.py SPY

API Explorer and API Pro keys can stream. One client is one connection,
so pass every symbol you need to the same client.
"""

import asyncio
import os
import sys

import itmatrix as itm
from itmatrix.filters import nearest, top


async def main(symbol: str) -> None:
    # The 20 book strikes nearest spot. The book holds the top strikes by gross
    # GEX, so a near-spot strike outside that set is not in it.
    async with (
        itm.AsyncITMClient(api_key=os.environ["ITM_API_KEY"]) as client,
        client.stream.gex_book(symbol, filter=nearest(20)) as book,
    ):
        frames = 0
        async for state in book:
            frames += 1
            print(
                f"{state.symbol} v{state.version} spot={state.spot:.2f} "
                f"net_gex={state.net_gex:,.0f} flip={state.flip_point} "
                f"changed={len(state.changed)}"
            )
            for strike, call, put, total in zip(
                state.strikes, state.call_gex, state.put_gex, state.gex
            ):
                print(f"  {strike:>9g} {call:>16,.0f} {put:>16,.0f} {total:>16,.0f}")
            if frames == 3:
                # Swap the filter on the live subscription: no resubscribe.
                book.set_filter(top(10, by="gross"))


if __name__ == "__main__":
    try:
        asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "SPY"))
    except KeyboardInterrupt:
        pass
    except itm.StreamError as error:
        # `reason` names a refusal such as client_outdated or connection_limit.
        sys.exit(f"{error.code} {error.reason or ''}: {error.message}")
