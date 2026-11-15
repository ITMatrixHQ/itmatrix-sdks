"""Print SPY's live GEX book every time it changes.

    pip install -U "itmatrix[stream]"
    ITM_API_KEY=itm_... python gex_stream.py

Ctrl-C closes the book and the script exits cleanly.
"""

import asyncio
import os

import itmatrix as itm


def show(book: itm.Book) -> None:
    print(book)
    for row in book:
        print(f"{row.strike:>8g} {row.call:>15,.0f} {row.put:>15,.0f} {row.total:>15,.0f}")


async def main() -> None:
    async with itm.AsyncITMClient(api_key=os.environ["ITM_API_KEY"]) as client:
        spy = client.stream.gex("SPY", nearest=20)
        spy.on("update", show)
        spy.on("status", lambda status: print("status:", status))
        spy.on("error", lambda error: print("error:", error))
        await client.run()


if __name__ == "__main__":
    asyncio.run(main())
