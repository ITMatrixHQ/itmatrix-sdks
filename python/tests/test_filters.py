"""Strike filters built from the keyword arguments of stream.gex(...)."""

import unittest
from dataclasses import fields, replace
from datetime import datetime, timezone

import itmatrix as itm
from itmatrix._core.filters import build
from itmatrix._core.gex_book import GexBookState


def f(**options):
    built, _ = build(options)
    return built or (lambda state: state)


def all_strikes():
    return f()


def nearest(n):
    return f(nearest=n)


def within_pct(p):
    return f(within=p)


def between(lo, hi):
    return f(between=(lo, hi))


def top(n, by=None):
    return f(top=n, by=by)

# Spot 601.25. Strikes in thousandths with call and put sides.
STRIKES = (580_000, 595_000, 600_000, 602_500, 605_000, 610_000, 620_000)
CALL = (1.0e6, 12.5e6, 250.0e6, 187.5e6, 62.5e6, 31.25e6, 90.0e6)
PUT = (-300.0e6, -125.0e6, -93.75e6, -46.875e6, -15.625e6, 0.0, -1.0e6)


def book(
    spot: float = 601.25, changed: tuple[int, ...] = (1, 4, 6)
) -> GexBookState:
    return GexBookState(
        symbol="SPY",
        book_id=42,
        version=8,
        captured_at=datetime(2025, 11, 1, 12, tzinfo=timezone.utc),
        spot=spot,
        net_gex=-1.5e9,
        flip_point=598.5,
        strikes=tuple(s / 1000 for s in STRIKES),
        strikes_thousandths=STRIKES,
        call_gex=CALL,
        put_gex=PUT,
        gex=tuple(c + p for c, p in zip(CALL, PUT)),
        changed=changed,
        reason=None,
    )


HEADER = (
    "symbol",
    "book_id",
    "version",
    "captured_at",
    "spot",
    "net_gex",
    "flip_point",
    "reason",
)


class FilterTests(unittest.TestCase):
    def assert_view(self, view: GexBookState, strikes: list[float]) -> None:
        state = book()
        for name in HEADER:
            self.assertEqual(getattr(view, name), getattr(state, name), name)
        self.assertEqual(list(view.strikes), strikes)
        rows = [state.strikes.index(s) for s in strikes]
        self.assertEqual(view.strikes_thousandths, tuple(STRIKES[i] for i in rows))
        self.assertEqual(view.call_gex, tuple(CALL[i] for i in rows))
        self.assertEqual(view.put_gex, tuple(PUT[i] for i in rows))
        self.assertEqual(view.gex, tuple(state.gex[i] for i in rows))

    def test_all_is_the_whole_book(self) -> None:
        state = book()
        self.assertEqual(all_strikes()(state), state)

    def test_nearest(self) -> None:
        self.assert_view(nearest(3)(book()), [600.0, 602.5, 605.0])
        self.assertEqual(nearest(1)(book(spot=602.0)).strikes, (602.5,))
        self.assert_view(nearest(0)(book()), [])
        self.assertEqual(nearest(50)(book()), book())

    def test_nearest_ties_go_to_the_lower_strike(self) -> None:
        # 601.25 is 1.25 from both 600 and 602.5.
        self.assert_view(nearest(1)(book()), [600.0])
        self.assertEqual(nearest(1)(book(spot=597.5)).strikes, (595.0,))

    def test_within_pct(self) -> None:
        # 1% of 601.25 is 6.0125: 595.2375 to 607.2625.
        self.assert_view(within_pct(0.01)(book()), [600.0, 602.5, 605.0])
        self.assert_view(within_pct(0.02)(book()), [595.0, 600.0, 602.5, 605.0, 610.0])
        self.assert_view(within_pct(0)(book()), [])
        self.assert_view(within_pct(0.5)(book()), [s / 1000 for s in STRIKES])
        # The bound is inclusive: 600 / 400 - 1 is exactly 0.5.
        self.assertEqual(
            within_pct(0.5)(book(spot=400.0)).strikes, (580.0, 595.0, 600.0)
        )

    def test_within_pct_without_a_spot_is_empty(self) -> None:
        self.assertEqual(within_pct(0.1)(book(spot=0.0)).strikes, ())

    def test_between_is_inclusive(self) -> None:
        self.assert_view(between(600, 605)(book()), [600.0, 602.5, 605.0])
        self.assert_view(between(600.5, 604.9)(book()), [602.5])
        self.assert_view(between(700, 800)(book()), [])

    def test_top_by_each_measure(self) -> None:
        # gross: 580 301e6, 600 343.75e6, 602.5 234.375e6, 595 137.5e6
        self.assert_view(top(2)(book()), [580.0, 600.0])
        self.assert_view(top(2, by="gross")(book()), [580.0, 600.0])
        # net |call+put|: 580 299e6, 602.5 140.625e6, 600 156.25e6, 595 112.5e6, 620 89e6
        self.assert_view(top(2, by="net")(book()), [580.0, 600.0])
        self.assert_view(top(3, by="net")(book()), [580.0, 600.0, 602.5])
        self.assert_view(top(2, by="call")(book()), [600.0, 602.5])
        self.assert_view(top(2, by="put")(book()), [580.0, 595.0])
        self.assert_view(top(0)(book()), [])

    def test_top_ties_go_to_the_lower_strike(self) -> None:
        state = replace(book(), call_gex=(5.0,) * 7, put_gex=(0.0,) * 7, gex=(5.0,) * 7)
        self.assertEqual(top(2)(state).strikes, (580.0, 595.0))

    def test_changed_indexes_the_view(self) -> None:
        # Book positions 1 (595), 4 (605) and 6 (620) changed.
        view = between(595, 605)(book())
        self.assertEqual(view.strikes, (595.0, 600.0, 602.5, 605.0))
        self.assertEqual(view.changed, (0, 3))
        self.assertEqual(nearest(2)(book()).changed, ())

    def test_filters_are_pure(self) -> None:
        state = book()
        copy = replace(state)
        for f in (nearest(2), within_pct(0.01), between(1, 2), top(3, by="put")):
            f(state)
            f(state)
        self.assertEqual(state, copy)
        self.assertEqual(nearest(2)(state), nearest(2)(state))

    def test_view_has_the_state_shape(self) -> None:
        view = nearest(2)(book())
        self.assertIsInstance(view, GexBookState)
        self.assertEqual(
            [f.name for f in fields(view)], [f.name for f in fields(GexBookState)]
        )

    def test_top_combines_with_a_window(self) -> None:
        self.assertEqual(f(nearest=3, top=1, by="call")(book()).strikes, (600.0,))
        self.assertEqual(build({"within": 0.01, "top": 2})[1], {"within": 0.01, "top": 2, "by": "gross"})
        with self.assertRaises(ValueError):
            build({"nearest": 3, "between": (1, 2)})
        with self.assertRaises(ValueError):
            build({"by": "net"})
        with self.assertRaises(TypeError):
            build({"near": 3})

    def test_invalid_arguments_raise_at_construction(self) -> None:
        for make in (
            lambda: nearest(-1),
            lambda: within_pct(-0.01),
            lambda: within_pct(float("nan")),
            lambda: between(605, 600),
            lambda: between(float("nan"), 600),
            lambda: top(-1),
            lambda: top(5, by="oi"),  # type: ignore[arg-type]
        ):
            with self.assertRaises(ValueError):
                make()
        for make in (
            lambda: nearest(2.5),  # type: ignore[arg-type]
            lambda: nearest(True),  # type: ignore[arg-type]
            lambda: within_pct("2%"),
            lambda: f(between=600),  # type: ignore[arg-type]
            lambda: between("590", 610),  # type: ignore[arg-type]
        ):
            with self.assertRaises(TypeError):
                make()

    def test_old_filter_names_are_gone(self) -> None:
        for name in ("filters", "GexBookFilter", "GexBookSubscription"):
            self.assertNotIn(name, itm.__all__)


if __name__ == "__main__":
    unittest.main()
