# LOB-X in Plain Language — The Team Guide

**Read this before anything else.** It explains the whole codebase for people
who are neither quants nor low-level engineers. Every section starts with a
plain analogy, then names the real terms used in code, then shows the actual
step-by-step process. Technical details live in `docs/explainer.md`; this is
the readable layer above it. One sitting ≈ 45 minutes.

**A note on truth:** everything described here exists in this repo and was
verified by reading the code and running it. Where something is *not yet*
built, we say so. The most important honest fact up front: **our strategies
currently lose money at retail fees, by design of the market, not by bug**
(§6 explains exactly why, with numbers).

---

## 0. Main characters (read this table first)

| Role in code | Folder | Analogy |
|---|---|---|
| Data taps | `lobx/market_data/` | reporters at the market, live-keeping notes |
| Notebooks | `data/history/`, `storage/` | the archive shelf |
| Prep kitchen | `lobx/strategy/features.py` | chopping ingredients → measured bowls |
| Recipe book | `lobx/strategy/` | 7 ways to decide your prices |
| Flight simulator | `lobx/execution/simulator.py` | practicing without crashing |
| The real exchange (ours) | `backend/cpp/` | actual trading floor we built |
| Science referee | `lobx/backtest/research.py` | fair testing of recipes |
| Dashboard | `lobx/terminal/`, `scripts/lobx_cli.py` | the shop window |
| Hitchhiker API | `lobx/api/` | drive-through window for outsiders |

---

## 1. The elevator pitch (analogy)

Imagine you run a **currency-exchange booth** in an airport. You make money
by posting two prices: *we buy euros at 1.0850*, *we sell euros at 1.0870* —
and collecting the difference whenever travelers trade with you.

Three things can kill you:

1. **Fees** — the airport taxes every exchange (that's *maker fees*: what the
   venue charges you for posting an order they filled from their book).
2. **Being run over** — a trader who knows something you don't buys all your
   euros right before the euro rises. You sold cheap. In code this is called
   **adverse selection**: the people who trade against you pick exactly the
   moments they were right about. This is the #1 killer, and almost every
   loss in this project comes from it, not fees.
3. **Inventory** — you're holding a pile of euros that may fall in value.

LOB-X is a practice booth: it watches the *real* market (Binance's Bitcoin
exchange), learns the market's habits (how volatile, how fast people slam
orders, when they rush), and computes **the two prices you should post right
now** — then tests those prices in a flight-simulator before anyone risks
money, and honestly reports whether the recipe would have made or lost.

---

## 2. The whole system on one page

```
   ┌───────────────────── REAL WORLD SOURCES ─────────────────────┐
   │  Binance live websockets (spot)    Binance public archive    │
   │  "right now" data                  (historical data, zips)   │
   └───────────────┬──────────────────────────────┬──────────────┘
                   │                              │
                   ▼                              ▼
        ┌────────────────────┐          ┌────────────────────┐
        │ DATA TAPS          │          │ DATA ARCHIVE TOOL  │
        │ market_data/       │          │ fetch_history.py   │
        │ (ws + repair kit)  │          │ (downloads days)   │
        └─────────┬──────────┘          └─────────┬──────────┘
                  │ clean, ordered                │ notebook
                  │ book data                     │ files
                  ▼                               ▼
        ┌──────────────────────────────────────────────────┐
        │              PREP KITCHEN  (features.py)          │
        │  turns raw ticks into 5 small numbers per moment:  │
        │  σ volatility · Hawkes burst meter · microprice    │
        │  order-book imbalance · trade rate                │
        └──────────────┬───────────────────────┬───────────┘
                       │                       │
        ┌──────────────▼─────────┐   ┌────────▼───────────┐
        │ RECIPE BOOK            │   │ CALIBRATION        │
        │ strategy/ (7 recipes)  │◄──│ analytics/         │
        │ → two prices to post   │   │ (measures market   │
        └──────────────┬─────────┘   │ habits from data)  │
                       │             └────────────────────┘
                       ▼
        ┌──────────────────────────────────────────────────┐
        │  FLIGHT SIMULATOR  (execution/simulator.py)       │
        │  replays the market with cruel realism:           │
        │  network delay · queue games · fees · no cheating │
        └──────────────┬───────────────────────────────────┘
                       │ scores per recipe
                       ▼
        ┌──────────────────────────────────────────────────┐
        │  SCIENCE REFEREE  (backtest/research.py)          │
        │  train on past → test on unseen → statistics      │
        │  → results/research/*.json + summary tables       │
        └──────────────┬───────────────────────────────────┘
                       │
       ┌───────────────┼────────────────────┐
       ▼               ▼                    ▼
  SHOP WINDOW     PLOTS/BARS            NOTE
  terminal/ TUIs  results/analysis/     (honest reports or
  scripts/lobx_   + terminal bar charts  "this scared us, stopped")
  cli.py
```

Also in the building, side rooms:
- **Our own trading floor** (`backend/cpp/`, C++): a small real matching
  engine — what an exchange actually runs — used as the "opponent" in the
  simulator's API mode and in benchmarks.
- **Drive-through window** (`lobx/api/`): a web server so outsiders can send
  orders to our C++ engine over HTTP.
- **Shelves** (`storage/`, `data/`): notebooks on disk (parquet files) and an
  optional database (Postgres).

---

## 3. The data taps (modules 1–2 of the table)

**Analogy:** a reporter at the market with two walkie-talkies. One reads the
shouts ("best offer now 70,000.10 to buy!") — those are *book updates*. The
other reports actual sales ("just sold 0.5 BTC at 69,999.9 by a buyer walking
up to a seller!") — those are *trades* (specifically *market orders*:
someone accepted a posted price immediately rather than posting their own).

**Real terms:** the "shouts" stream is **bookTicker** (best bid/ask updates,
up to 10 per second). The "sales" stream is **trades**; when a sale prints as
"buyer is the maker", it means the *buyer* had posted a waiting order (*bid*)
and someone else hit it — the seller was the aggressor. That flag is gold,
because it tells us which direction the aggression came from.

**Process (4.1 websocket client, step by step):**
1. Connect to Binance's public data websocket (no account needed).
2. Subscribe to 2 rivers at once: `btcusdt@depth@100ms` + `btcusdt@trade`.
3. Loop forever: receive message → hand it to whoever is listening.
4. If the connection dies → wait 2s, 4s, 8s … (max 30s) → reconnect. A dead
   connection is now sensed within 30 seconds (this session's fix; it used
   to be 10 minutes of ghost-reading).

**The repair kit (4.2 depth sync) — the most delicate part.** The shouting
is sometimes interrupted. Suppose you miss 3 shouts — you now believe the
best price is 70,000.1 when it moved to 69,999.8. Every price you post after
that is wrong. So the connector maintains one golden rule:

> **Every book update carries a numbered stamp (update id). Numbers must
> never skip. If they skip → the notebook is a lie → throw it away and
> re-photograph the whole market.**

Algorithm steps:
1. Keep a cursor = the stamp of the last accepted update.
2. New update arrives:
   - stamp continues the sequence? → apply it (update prices in the book
     mirror); move cursor. Done.
   - stamp skips? → **gap**. Step 3.
3. On gap: erase the mirror, buffer the new update, and schedule a
   "re-photograph" (a REST snapshot request of the whole order book).
4. Re-photograph: GET the full book from Binance's REST API (with retries
   and 2 backup hosts if the main one is unreachable — fix from this
   session).
5. While photographing: keep buffering updates, never trust the book.
6. Fresh photo arrives → rebuild book from the photo → replay the buffered
   updates if their numbers connect to the photo → resume normal mode.
   If a gap appears even during the replay → abort, start over. (This
   abort-on-mid-replay behavior + "only one repair job at a time" are fixes
   we made this session.)

Why so strict? Because a corrupt book silently poisons every number
computed downstream. Better a brief black screen than confident nonsense.

**Storage** (`storage/`): the mirrored top-of-book and every trade are
appended to compressed notebook files (parquet — a fast column format,
think "spreadsheet, zipped, designed for analysis"). An optional database
(Postgres) copy exists. Neither ever blocks the live feed: if the database
sneezes, the warning is logged and the feed keeps flowing.

---

## 4. The prep kitchen — features (module 3)

**Analogy:** raw shouting is noise. The kitchen distills each moment into
five measured bowls a cook can actually use.

Each tick, `features.py` produces:

1. **σ ("sigma") — volatility.** *How fast is the price wobbling right now,
   in dollars per second-of-spread?* Measured by an exponentially weighted
   watch: recent seconds weigh more than old ones (half-life ≈ 2 minutes:
   news two minutes old counts half as much). **Fix this session:** when the
   feed goes silent, the kitchen used to count "no movement" — which diluted
   σ like watering-wine; now it skips the silent periods entirely.
2. **The burst meter — Hawkes intensity.** *How busy is the crowd right now
   versus its usual level?* Self-exciting model in plain words: when 5 trades
   slam in within a second, the *probability of another trade right now
   rises* (excitement is contagious). The code keeps a decaying counter that
   spikes +α per trade and decays at rate β; the *ratio* of current to usual
   intensity is displayed as QUIET / NORMAL / ELEVATED / BURST. (The real
   name: an exponential Hawkes process, with its 3 knobs (μ, α, β) fitted to
   data by maximum likelihood — "tune the knobs so the story best explains
   what actually happened".)
3. **Microprice.** The fair middle of the shouting, *weighted by queue size*:
   if the buy shout is bigger, the true price leans toward the bid. Real
   name: size-weighted mean of bid and ask.
4. **Order-book imbalance.** Are there more buyers queuing or sellers?
5. **Trade rate** — market orders per second, the crowd speed.

The five bowls call themselves **MarketState** in code. One decisive design
rule: **every recipe (strategy) sees the exact same bowls** — live and
simulated — so results are comparable and there's one kitchen to maintain.

---

## 5. The recipe book — strategies (module 4)

**Analogy:** a market-stand owner deciding where to stand in relation to the
current crowd price. Stand too close → you trade constantly but the *most
rushed trades* (which are exactly the ones they know something about) burn
you. Stand too far → nobody ever trades with you, you earn nothing. Every
recipe below is a different answer to that dial. All recipes output the same
thing: **(bid price, ask price)** — rounded so they *wait* politely instead
of barging (real term: **post-only / passive** — if your "buy" price would
immediately execute, the code steps it down one tick instead).

The seven recipes (by increasing sophistication):

1. **NoTrade** — quotient nothing. The control group. Its full-time job:
   to give every comparison a floor.
2. **FixedSpread** — quote "mid minus 5¢ / mid plus 5¢" forever. The naive
   baseline. Cheapest to run, loses the most (§6).
3. **InventorySkew** — the honest tilt. Holding inventory → both quotes
   slide down (so your *ask* is closer and likely to fill = selling stock
   down) — lean proportional to how much you hold. Real term: inventory
   skew, quoted in USD-per-lot-held.
4. **Avellaneda–Stoikov (AS)** — the classic academic recipe (1997–2001).
   The reservation **r = mid − q·γ·σ²·τ** is your personal fair price
   (long inventory → you mentally accept a lower internal price; γ is your
   fear factor, τ the horizon τ=60s). The spread δ grows with volatility
   (wobblier market → wider stand) and with how quickly people find
   stand-art loudness from far away (real term: the arrival-decay constant
   κ, measured in *per dollar of distance*).
5. **GLFT** (Guéant–Lehalle–Fernandez–Tapia, 2013). Same philosophy, but
   the *liquidity penalty uses measured foot traffic*. Our data says "of
   the crowd running to the mid, the share reaching depth d dollars away
   is λ(d) = A·e^(−k·d)" — A (how many slams per second reach the touch)
   and k (how fast interest dies with distance) are *both fitted from the
   real data*, not copy-pasted from a paper. This recipe quotes off mid but
   also with an inventory skew (bid retreats when long).
6. **GLFT + Hawkes + FairValue** — the flagship. Adds:
   - **Fair-value lean (α):** a small OLS-predicted nudge to the mid from
     the kitchen's bowls (microprice deviation, flow imbalance, book
     imbalance) — "the crowd is leaning in, value is a hair above mid".
   - **Widen in bursts:** when the Hawkes ratio > 1, multiply both quote
     depths by `1 + widen·(ratio−1)` — "crowd rushing → step back".
   - **Pause in frenzy:** if ratio ≥ 3, quote only the side that
     *unwinds* inventory (don't add risk while people stampede).
7. **GLFT + HFV fee-aware** — same as 6, plus at the end: check each side
   against the fee bill. *If my edge per side is smaller than the fee I'd
   pay, don't post that side.* This is the strategy that truthfully answers
   +0.00 (does nothing at all) — because at retail fees on BTC, quoting is
   genuinely a losing business. **That's the single most respected line in
   the whole results table.**

**Also in the book:** an **AS + Hawkes** variant — classical AS quotes
widened when the burst meter rises, and in a frenzy it only quotes the
side that unwinds inventory. Merged and measured (§10).

---

## 6. The flight simulator (module 5) — why this is where trust lives

**Analogy:** you don't learn to fly on a passenger jet. The simulator replays
recorded days of real market and makes your strategy fly through it — with
cruel realism. Nothing is printed "as if" — fills, rejects, and costs are
emulated with the same dirtiness as the real venue.

The simulator's cruelty (i.e., its realism):

1. **Delay.** Your market view is up to 10 ms old; your order leaves, then 10
   ms later lands at the exchange. A stale quote is a gift to people who can
   see the present.
2. **The polite line (queue position).** When you post at the best price,
   everyone already waiting there is *in front of you*. Plain-language model:
   when the displayed queue shrinks, that shrink is either *trades* (you only
   get filled after the people ahead are served) or *cancellations* (some of
   the shrink probably happened ahead of you, in proportion to 1 −
   (front/total)**ⁿ — "if you're near the back, most quitters were ahead of
   you"; n≈3 tuning power — the real name: the Rigtorp / hftbacktest
   probabilistic queue model).
3. **The so-what test (post-only reject).** Post a buy *above* the current
   best ask? Rejected. Costs you an opportunity, not money — the sim makes
   you pay attention anyway (it counts).
4. **Fees.** Every fill pays the maker fee. Or earns a rebate at bigger
   volume tiers (fee profiles exist for both).
5. **Kill switches.** If peak-to-now losses exceed a limit → cancel
   everything, dump inventory, halt. (Also: position cap. Also: at session
   end, all inventory is closed out at the touch — with the fix from this
   session that it no longer tries to "sell" into a book that was never
   seen, which used to book a fake catastrophic loss.)

**Order's journey, numbered (this is the heart of the app):**
1. Every 100 ms: strategy gets the bowls + its inventory → quotes.
2. Compare with what's already pending: changed materially? → cancel old,
   send new. (Cancel path also has simulated latency.)
3. Order enters a departure lounge with `arrive_at = now + 10ms`.
4. At its arrival time: if it would immediately trade → **rejected**
   (post-only); otherwise it joins the book either *ahead* of the displayed
   queue (better price) or *behind it* (equal price).
5. Trades sweep in. When a trade reaches our price: people ahead first, then
   maybe us → **fill** (fees applied, position updated).
6. Every second: account marked to market → equity curve, drawdown switch.
7. At the end: flatten, and the scoreboard is produced (below).

**The scoreboard the referee uses (plain words):**
- **Net PnL** — real dollars after fees.
- **Fills/hour** — how often the market accepted our posted prices.
- **Edge per fill** — average profit *promised* at the moment of the fill
  (price we got vs the fair mid then).
- **Adverse (5-s markout)** — average of *how much the fair mid moved
  against us within 5 seconds* of each fill. **This is the kill count.**
- **Sharpe / drawdown** — how smooth / how deep the pain.
- **95% CI** — see referee, §7.

### Why our strategies lose: the honest root cause analysis

Look at the flagship's per-fill columns from the real out-of-sample run
(VIP0 fees = 2bp maker / 5bp taker, quotes of 0.001 BTC on ~$70,000 BTC):

```
strategy        promised edge/fill    adverse paid in 5 s
FixedSpread     +16 µ$                +3,586 µ$
InventorySkew  +37 µ$                 +4,226 µ$
AS             +754 µ$                 +4,813 µ$
GLFT           +725 µ$                 +4,560 µ$
GLFT+Hawkes+FV +976 µ$                 +4,854 µ$   ← least-worst: best
GLFT+HFV fee-aware: does nothing (+0.00)            edge capture
```

Reading it: **even the best recipe captures ~1,000 µ$ per fill and pays
~4,800 µ$ per fill to the future.** The gap (~3.8 $ of 0.001 BTC, i.e.
~5.5 bp) is almost exactly the sum of the 2 bp maker fee + ~3.5 bp of
adverse-selection cost. **This is why all seven lose by different
amounts:** they differ in how much *edge* they capture (GLFT+FV captures
60× more edge per fill than FixedSpread — it only fills when the fair value
leans toward us — which is why it loses least), but none of them has yet
solved *who is on the other side*. On BTC, daytime retail flow is largely
informed/toxic flow, and the venue's tick (0.10 $ = 0.14 bp!) is far too
fine to expect the spread itself to pay 2 bp of fees.

The ranking you noticed is exactly the theory:
FixedSpread (worst — fills constantly with everyone) → InventorySkew →
GLFT → AS → GLFT+Hawkes+FV (least loss — most selective quoting) →
fee-aware (zero loss = chooses correctly to not play a rigged game).

**What this means, and the plan to fix it (all in §10):** widen the *who*
of quoting, not just the *where* — selective one-sided quoting, toxicity
measurement per quote, better L2-aware queue knowledge, bigger size under
volume-tier fees (rebates), and crash-style windows where volatility widens
spreads faster than costs.

---

## 7. The science referee (module 6) — how claims are checked

**Analogy:** every used-car-salesman result historically gets the referee's
treatment: *prove it on data the recipe never saw, under pessimistic
assumptions, with error bars, and if the fear test fails, say so.*

Steps (this is `research.py`):
1. **Split the day** 08:00–12:00 into the *training* half (08–10) and the
   *exam* half (10–12) — real-name: in-sample (IS) / out-of-sample (OOS).
2. **Measure the market's habits** on the training half only (σ, A, k, the
   two Hawkes knob-sets, the fair-value lean coefficients).
3. **Pick the fear dial** (γ) by tournament on the training half: try 11
   values, keep the best net PnL *among dials that actually trade at least
   100 times an hour* (without that floor, meek no-trading settings win by
   doing nothing — a classic fake victory).
4. **Freeze everything.** Then run the exam half as if the models had never
   seen it. Nobody anywhere is allowed to peek at the exam for tuning.
5. **Error bars = the bootstrap.** Per-minute profits over the exam are cut
   into 10-minute blocks; blocks are reshuffled like a deck 10,000 times;
   the 2.5%–97.5% range of the result is the **95% confidence interval**.
   Plain reading: "if luck were the only cause, here's the range of stories
   luck could tell." An interval entirely under zero ⇒ the loss is real,
   not one bad afternoon. Same machinery decides *is strategy X actually
   better than the naive baseline* (paired comparison).
6. **Stress tests:** the best recipe again, but with 150 ms delays, an
   optimistic queue ("fills arrive too easily"), slow re-quoting. A fragile
   strategy crumbles in stress; a robust one degrade gracefully.
7. **Save everything** to `results/research/*.json` + `SUMMARY.md`,
   including the *reasons*: calibrated numbers, chosen γ, OOS seconds, CI.

Output honesty note: the JSON deliberately stores the summary, not the raw
per-minute arrays (too big); the plotting script re-runs the exam half with
frozen parameters to regenerate curves when plots are wanted.

---

## 8. The other rooms

- **Our C++ trading floor** (`backend/cpp/`): an actual matching engine —
  accepts orders, keeps the book, matches crosses. Speed measured: posting
  one order from Python costs ~3 microseconds typical, ~9 µs in the worst
  1%. Half a million orders/second sustained. A resting order costs 0.14 KB
  of memory (fixed this session; the benchmark used to measure nothing
  because its test orders accidentally kept trading against each other).
- **Drive-through window** (`lobx/api/`): a small web server (FastAPI):
  POST an order → the C++ engine acknowledges; websocket broadcasts fills.
  Also demonstrates the security fix from this session: nonsense orders
  (price ≤ 0 / qty ≤ 0) are rejected at the door with HTTP 422.
- **Shop window** (`terminal/` + `scripts/lobx_cli.py`):
  - `live` — glass counter: candles, tape, book, burst meter, animated.
  - `sim` — the practice counter: all strategies trading the live feed
    side by side; **on quit it prints bar charts in the terminal itself**
    (built this session): net-PnL bars, edge-vs-adverse two-sided bars, and
    a minute-by-minute colored strip. Previously, nothing got printed at
    all on exit and the session notebook never got closed.
  - `backtest` — run a full referee cycle from the command line.
  - `quote` — "what would the models say right now?" (live mid + stored
    habits + math). Warns if you ask about a non-BTC symbol because the
    stored habits are BTC's.
  - `scan`, `book`, `replay`, `sessions`, `status`.

---

## 9. Three recorded journeys (step by step)

### Journey A — one tick of live paper trading
1. WebSocket delivers a book update (best bid/ask changed).
2. `collector` routes it: mirror repair-check → book mirror → bowls update
   (σ tick, imbalance) → notebooks written → sim notified.
3. WebSocket also brings a trade: burst meter +α, decaying.
4. Every 100 ms the sim asks: warm-up done? calibration fresh? If a refit is
   due, the heavy math runs in a *worker thread* so the feed never stops
   (this is the only real parallelism in live mode).
5. For each of the 7 strategies: bowls + that account's inventory → two
   prices → send to the sim (which emulates latency/queues on each account
   independently — one account's fill never touches another's).
6. TUI paints the whole shop from the same state — nothing is computed twice.

### Journey B — one backtest run
1. Load the calm day from the note shelves (1.1 million book rows + 84k
   trades).
2. Feed them to the simulator in exact timestamp order (~16k events/sec;
   the **same** engine and bowls as live).
3. All 7 accounts (strategies) consume the same stream simultaneously,
   each keeping its own fills/equity.
4. `finish()`: flatten everything; compute the scoreboard columns.
5. Print table + terminal bar charts.

### Journey C — one research campaign (the referee)
1. Download the two days (calm + crash) from Binance's archive into
   notebooks (hash-sums recorded in `manifest.json`).
2. Per window: referee steps 1–7 from §7.
3. Also cross the regimes: habits learned in the calm day, *applied without
   modification* to the crash day (harsher exam: trains on peace, tests in
   war).
4. Produce `results/research/*.json`, `SUMMARY.md`, and (this session) the
   plot pack + terminal-bar version.
5. Honest final answer: *at VIP0 BTC fees, on the calm day, all quoting
   recipes lose; the best ones lose least by being picky; the fee-aware one
   declines to play; the CIs prove the losses are real, not luck.*

---

## 10. Known truths & the to-do list (so nobody relitigates)

**True today:**
1. All infrastructure is real and tested: connectors, repair kit, storage,
   simulator, C++ engine, referee, dashboards, bars, API validation. 233
   automated tests pass, including regression tests written for every fix
   in this session.
2. At retail BTC fees (VIP0), no posting strategy is profitable in either
   regime; the fee-aware strategy's decision to abstain is correct
   behavior, not a bug.
3. **Genuinely positive out-of-sample results exist** (2024-03-05 crash
   window, maker-rebate fee tier, re-fetched and checksummed data): AS
   nets **+$1.19** (95% CI [+0.04, +2.33]) and GLFT nets **+$4.64**
   (95% CI [−0.06, +8.62]), both 100% of bootstraps better than
   touch-quoting (−$79.7). Scale grows roughly linearly with quote size
   until market impact — larger capacities need larger-size tests.
4. The new **AS+Hawkes** variant (burst-widened AS with same frenzy-pause
   rules) is the **best retail-tier quoter in the crash**: −$4.07 vs
   −$9.39 plain AS and −$18.33 plain GLFT, by trading ~2.5× less in
   frenzies. At the rebate tier it trades too little to beat plain AS.

**Roadmap status (updated):**
1. ~~L2 upgrade~~ — `bookDepth` daily archive confirmed available
   (§10frontnote); integration into the queue model is the remaining work.
2. ~~AS + Hawkes~~ — **merged** (strategy/avellaneda.py + research suite).
3. Profitability hunt — **achieved OOS-positive at maker-rebate tier**
   (item 3 above); remaining: scale verification and calm-regime profit.
4. ~~Plot organization~~ — done: each visualize run writes
   `results/analysis/sessionN/` (legacy layout auto-migrated).
5. ~~CI pipeline~~ — done: `.github/workflows/ci.yml` (pytest + ruff on
   Python 3.10/3.11/3.12, Windows/Linux; C++ extension build smoke test);
   `requires-python` corrected to the supported floor (≥3.10).
6. Hawkes MLE speed-up (numba) for multi-million-event fits.

---

## 11. Pocket glossary (plain → real)

| Plain | Real term / symbol |
|---|---|
| The two shouts | best bid / best ask; their middle = **mid**, gap = **spread**, unit of price = **tick** |
| A waiting order | passive/post-only order; filled one = maker; the barger = taker (market order) |
| The airport tax | fees, in basis points (**bp** = 1/100 of a percent); maker rebate = paid *to* you |
| Sold cheap because they knew | **adverse selection**; measured by **markout** (fair-mid drift against you after a fill) |
| Price wobble speed | **volatility σ** (USD per √second in this code) |
| Fear dial | **γ** (gamma), risk aversion, 1/USD |
| Interest fading with distance | arrival law **λ(d) = A·e^(−k·d)**; **A** intensity at touch, **k** decay per USD |
| Excitement is contagious | **Hawkes process**; ratio > 1 = elevated, ≥ 3 = frenzy |
| Your personal fair price | **reservation price** r = mid − q·γ·σ²·τ |
| Crowd lean | **order-book imbalance**, **microprice** (size-weighted bid/ask), **flow imbalance** (from Hawkes) |
| Parallel universe test | the **simulator** (event-driven, latency + queue models + post-only) |
| Fair testing | in-sample vs **out-of-sample**; **95% CI** via **moving-block bootstrap** |
| Pain depth | **drawdown**; peak-to-trough pain in money |
| Repair kit | **depth resync** via **update-id continuity** |
| Notebooks | **parquet files** (compressed columnar tables) |

---

*Written 2026-11-01, after the audit-and-repair session. Every claim above
maps to code in the repo; technical cross-references are in
`docs/explainer.md`.*
