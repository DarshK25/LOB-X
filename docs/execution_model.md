# Execution simulator

`lobx/execution/simulator.py` is the paper exchange behind both historical
backtests and live paper trading. A backtest is only as believable as its fill
model, and the original fill model was the main reason earlier results could
not be trusted. This page lists what is modelled, what is not, and how much
each assumption matters.

## Structure

```
MarketSimulator(features, accounts)
  on_book(ts, bid, bid_qty, ask, ask_qty)   → every account: arrivals/cancels, book-cross fills, queue update
                                             → every 100 ms: one MarketState → every strategy decides
  on_trade(ts, price, qty, is_buyer_maker)  → every account: trade-driven fills
SimAccount(strategy, ExecutionConfig)        one strategy, its own orders, cash, inventory, fees
```

All accounts see the same events and the same `MarketState`, so a seven-way
strategy comparison is one pass over the data, and differences between
accounts come only from the strategies. The live `sim` command runs exactly
this code on the Binance websocket feed. `FeatureEngine` is shared too, so live
and historical results use the same σ, Hawkes and imbalance code.

## What is modelled (`ExecutionConfig`)

| Effect | Default | Implementation |
|---|---|---|
| Latency | feed 5 ms + order 10 ms | an order decided at t goes live at t + 15 ms |
| Cancel latency | same round trip | a cancelled order can still fill until the cancel lands |
| Post-only | on | an order that would cross the book on arrival is rejected |
| Tick size | from data | strategies round outward; quotes are always passive |
| Queue position | probabilistic, power 3 | see below |
| Trade fills | | a sell aggressor at or through our bid fills us (subject to queue), never a buy aggressor |
| Book-cross fills | | if the opposite best price crosses our resting price, we were filled |
| Partial fills | | a trade fills at most its own quantity across our orders |
| Fees | per profile | maker fee on passive fills (negative = rebate), taker fee on liquidations |
| Rate limit | 100 ms per side | plus a requote only when the price moves at least one tick |
| Position limit | 0.01 BTC (10 lots) | the side that would breach it is not quoted |
| Kill switch | optional | drawdown limit → cancel all + taker liquidation |
| End of run | flatten | open inventory is closed with a taker order at the touch |

Fee profiles (`execution/fees.py`), Binance USD-M futures schedule:

| Profile | Maker | Taker |
|---|---|---|
| `vip0` | 2.0 bp | 5.0 bp |
| `vip9` | 0.0 bp | 1.7 bp |
| `mm_rebate` | −0.5 bp | 1.7 bp |
| `spot_vip0` | 10 bp | 10 bp |
| `zero` | 0 | 0 |

## Queue model (`execution/queue_model.py`)

Public data shows the size at a price, not where our order sits in it. The
probabilistic model follows Rigtorp (2013) and hftbacktest's `ProbQueueModel`:

* When we join a level we are at the back: `front` = displayed size.
* Trades at our price consume `front` first. We fill only from the part of a
  trade that exceeds the size ahead of us.
* A size decrease not explained by trades is a cancellation. The share that
  happened *ahead* of us is 1 − p, with p = 1 − (front/(front+back))ⁿ and
  n = 3. Near the back, most cancels are ahead of us; near the front, most
  are behind.
* A size increase is new orders joining behind us.
* An order priced better than the touch is first in line (`front = 0`).

`queue_model="optimistic"` is the old behaviour: any trade at our price fills
us as if we were first in line. It is kept as an upper bound and runs as a
stress test.

### How much it matters

Calm window, out-of-sample, at the market-maker rebate tier:

| | FixedSpread net PnL | fills/hour | adverse selection |
|---|---|---|---|
| probabilistic queue | −$2.9 | 914 | 0.52 bp |
| optimistic queue | **+$54.2** | 9,046 | 0.05 bp |

Same strategy, same data, same fees. The optimistic queue gives 10× the fills
and turns a loss into a profit, because it fills us on trades that in reality
were absorbed by the orders queued ahead of us. Those fills look free of
adverse selection (0.05 bp). Under the realistic model, the fills we do get
are disproportionately the ones where the price moves through us. **Any
market-making backtest without a queue model should be read as an upper
bound.**

## Not modelled

* **Market impact of our own orders.** Our orders do not change the public
  book, and other participants do not react to them. At 0.001 BTC per quote
  against 1–10 BTC at the BTCUSDT touch this is small, but it is not zero.
* **Hidden or iceberg liquidity.**
* **Exchange-side jitter.** Latency is constant: no matching-engine queueing
  and no feed bursts.
* **Price impact of liquidation** beyond crossing the spread.
* **Depth behind the touch.** The history is L1 (bookTicker). An order at the
  touch sees its queue exactly. An order behind the touch cannot see the size
  at its own level. It fills only on trades *through* its price until its
  level becomes the touch, and from then on it is assumed to be at the back
  of the whole displayed queue. That is pessimistic: in reality we would be
  ahead of everything that joined after us. Tuned GLFT in calm BTCUSDT quotes
  about $4 (40 ticks) from the mid when flat, so its fill counts are a lower
  bound.
  FixedSpread quotes at the touch and is measured exactly. The collector
  records live L2 (`data/raw/depth_*.parquet`), and
  `backtest.data.load_capture` replays it, which removes this limitation for
  captured sessions.

## Metrics (`analytics/performance.py`)

For a passive fill with side s (+1 buy, −1 sell), quantity Q and price p:

```
edge          = s · (mid_at_fill − p) · Q          spread captured
markout(h)    = s · (mid_{t+h} − p) · Q            value after h
adverse(h)    = edge − markout(h)                  what the market took back
```

* Adverse selection is reported at h = 5 s, in USD and in bp of maker
  turnover.
* Sharpe is computed on 1-minute PnL and annualised by √525,600.
* Max drawdown is measured on 1 s equity samples.
* Break-even maker fee is (gross PnL − taker fees) / maker turnover. It is
  the fee or rebate at which a fee-blind strategy breaks even, and the best
  single number for the economics of a quoting style.

Sharpe over two hours is noisy. Every comparison therefore also gets a
moving-block bootstrap (10-minute blocks, 2,000 resamples) of per-minute PnL,
paired across strategies, giving a 95% interval for each strategy and
P(strategy beats FixedSpread).
