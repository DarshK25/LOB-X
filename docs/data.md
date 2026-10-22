# Data

## Historical windows (`data/history/`)

Real Binance USD-M futures data from the official public archive
(data.binance.vision), cut to a few hours per regime and stored as compressed
Parquet:

| Label | Window (UTC) | Book updates | Trades | Price range | Size |
|---|---|---|---|---|---|
| `calm` | 2024-03-30 08:00–12:00 | 1,135,887 | 84,013 | 69,873–70,255 | 5.8 MB |
| `crash` | 2024-03-05 13:00–17:00 | 7,402,248 | 1,493,487 | 64,537–69,350 | 39.4 MB |

* **Book.** `bookTicker`: every change of the best bid or ask, with its size,
  tick by tick (L1).
* **Trades.** `aggTrades`: every trade, with price, quantity and aggressor
  side.

The crash window covers the 5 March 2024 sell-off (69.35k to 64.5k inside the
window).
Volume in it is 21 times the calm window's. Together the two windows bracket
the regimes a market maker has to survive.

```bash
python scripts/fetch_history.py                                   # rebuild both windows
python scripts/fetch_history.py --window 2024-03-02 00:00 04:00 --label calm2
```

The script streams each daily zip, keeps only the requested hours and writes
`manifest.json` with row counts, SHA-256 hashes and source URLs, so results
are reproducible without committing data to git.

**Why L1 and why 2024.** Binance publishes futures `bookTicker` history only up
to March 2024 and does not publish L2 history for futures at all. L1 is
enough for this study because every strategy quotes at or near the touch,
where the displayed size is exactly what the queue model needs.

## Live capture (`data/raw/`)

`scripts/run_collector.py` keeps a sequence-checked local L2 book from the
Binance depth stream and records every level change and trade. A capture
replays through the same simulator with `lobx.backtest.data.load_capture`.
Captures are not committed. Files appear once the collector closes them.

## Loading

```python
from lobx.backtest.data import load_history, list_history
data = load_history("calm")          # MarketData: numpy arrays, time-sorted
ins, oos = data.split(0.5)           # chronological split
for ev in data.events(): ...         # ("B", ts, bid, bid_qty, ask, ask_qty) / ("T", ts, px, qty, is_buyer_maker)
```

At equal timestamps, trades are replayed before book updates. A trade is the
cause of the book change printed with it, so this ordering avoids filling an
order against a book state that already reflects the trade.
