"""Display grouping for the paper-trading dashboard."""
from types import SimpleNamespace

from lobx.execution.simulator import BID, ASK
from lobx.terminal.sim_dashboard import group_fills, quote_text


def fill(ts, side, px, qty, maker=True):
    return ("FixedSpread", (ts, side, px, qty, 0.0, px, maker))


def test_prints_of_one_sweep_become_one_row():
    prints = [fill(1_000, BID, 81184.0, 0.00007) for _ in range(12)]
    prints.append(fill(1_000, ASK, 81185.0, 0.001))
    rows = group_fills(prints)
    assert len(rows) == 2
    assert rows[0]["n"] == 12 and abs(rows[0]["qty"] - 0.00084) < 1e-9
    assert rows[0]["side"] == BID
    assert rows[1]["n"] == 1


def test_interleaved_strategies_and_nearby_prices_still_group():
    prints = []
    for i, px in enumerate((81167.72, 81167.84, 81168.00)):
        prints.append(("AS", (1_000 + i, BID, px, 0.00007, 0.0, px, True)))
        prints.append(("GLFT", (1_000 + i, BID, px, 0.00007, 0.0, px, True)))
    rows = group_fills(prints)
    assert len(rows) == 2
    assert rows[0]["name"] == "AS" and rows[0]["n"] == 3
    assert rows[0]["px_lo"] == 81167.72 and rows[0]["px_hi"] == 81168.00


def test_a_later_sweep_at_the_same_price_stays_separate():
    rows = group_fills([fill(1_000, BID, 100.0, 1.0), fill(3_000, BID, 100.0, 1.0)])
    assert len(rows) == 2


def test_quote_text_says_how_far_behind_the_touch():
    order = SimpleNamespace(side=BID, price=99.8, remaining=1.0, arrive_at=0)
    text = quote_text(order, touch=100.0, tick=0.1, now_ms=1_000).plain
    assert "99.80" in text and "2t back" in text
    ask = SimpleNamespace(side=ASK, price=100.1, remaining=1.0, arrive_at=5_000)
    assert "sending" in quote_text(ask, touch=100.0, tick=0.1, now_ms=1_000).plain
    assert quote_text(None, 100.0, 0.1, 0).plain == "—"
