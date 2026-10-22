"""The C++ mirror must never self-match while tracking the exchange book."""
import pytest

pytest.importorskip("lobx_cpp")

from lobx.market_data.engine_mirror import EngineMirrorBridge  # noqa: E402


def test_snapshot_then_crossing_diff_does_not_trade():
    m = EngineMirrorBridge()
    m.load_snapshot([["100.00", "1"], ["99.99", "2"]], [["100.01", "1"], ["100.02", "3"]])
    assert m.best_bid_price() == 100.00 and m.best_ask_price() == 100.01
    # price jumps up: new bids above the old asks arrive in the same event as ask removals
    m.apply_depth_event({"b": [["100.02", "5"]], "a": [["100.01", "0"], ["100.02", "0"],
                                                       ["100.03", "1"]]})
    assert m.trades_generated == 0
    assert m.best_bid_price() == 100.02 and m.best_ask_price() == 100.03
