"""Exchange-time aggregation and terminal geometry regressions for live charts."""
import math
import random
from io import StringIO

import pytest
from rich.cells import cell_len
from rich.console import Console
from rich.text import Text

START = 1_704_067_200_000  # 2024-01-01 00:00:00 UTC, on a five-second boundary.
BODY = set("█▀▄")
VOLUME = set("▁▂▃▄▅▆▇█")


def new_chart(**kwargs):
    # Import here so an absent new module yields test failures, not a collection error.
    from lobx.terminal.candles import AdaptiveTerminalChart

    return AdaptiveTerminalChart(**kwargs)


def lines(text):
    return text.split("\n", allow_blank=True)


def plot_sections(text):
    """Read the visible price/volume panes, without depending on private methods."""
    rows = lines(text)
    volume_heading = next(i for i, row in enumerate(rows) if "Vol (" in row.plain)
    prices = [row.plain.split("│", 1)[1] for row in rows[1:volume_heading]]
    volumes = [
        row.plain.split("│", 1)[1]
        for row in rows[volume_heading + 1:]
        if "│" in row.plain
    ]
    return prices, volumes


def occupied_price_rows(text):
    prices, _ = plot_sections(text)
    return [i for i, row in enumerate(prices)
            if any(ch in BODY or ch in "─│" or "\u2801" <= ch <= "\u28ff" for ch in row)]


def price_labels(text):
    rows = lines(text)
    volume_heading = next(i for i, row in enumerate(rows) if "Vol (" in row.plain)
    return [row.plain.split("│", 1)[0].strip().lstrip("›")
            for row in rows[1:volume_heading] if row.plain.split("│", 1)[0].strip()]


@pytest.fixture
def penny_bounce():
    def build(mode="candle"):
        chart = new_chart(mode=mode)
        patterns = ((81588.35, 81588.36), (81588.36, 81588.35),
                    (81588.35, 81588.36, 81588.35), (81588.36, 81588.35, 81588.36))
        # Ten minutes of real buckets: up/down bodies and dojis with penny wicks.
        for bucket in range(120):
            for trade, price in enumerate(patterns[bucket % len(patterns)]):
                chart.add_trade(price, .001, bool(trade % 2),
                                START + bucket * 5000 + trade * 100)
        return chart

    return build


@pytest.mark.parametrize("mode", ["candle", "line"])
@pytest.mark.parametrize("height", [36, 45])
def test_ten_minute_penny_bounce_occupies_only_a_few_price_rows(penny_bounce, mode, height):
    chart = penny_bounce(mode)
    text = chart.render(120, height=height)
    prices, _ = plot_sections(text)
    if height == 45:
        assert len(prices) >= 36  # Exercise a tall price pane, not just a small chart.
    occupied = occupied_price_rows(text)
    assert occupied
    assert max(occupied) - min(occupied) + 1 <= 3


def test_penny_bounce_axis_uses_cents_not_subcent_row_spacing(penny_bounce):
    text = penny_bounce().render(120, height=45)
    labels = price_labels(text)
    assert all(len(label.split(".")[-1]) == 2 for label in labels)
    values = [float(label.replace(",", "")) for label in labels]
    assert .20 < max(values) - min(values) < 1  # Calm, without multi-dollar padding.
    assert "›81,588.36" in text.plain


def test_unresolved_penny_bodies_are_thin_directional_marks(penny_bounce):
    text = penny_bounce().render(120, height=18)
    prices, _ = plot_sections(text)
    assert not any(ch in BODY for row in prices for ch in row)
    price_end = text.plain.index("\nVol (")
    styles = [text.get_style_at_offset(Console(), i)
              for i, ch in enumerate(text.plain[:price_end]) if ch == "─"]
    assert any("green" in str(style) for style in styles)
    assert any("red" in str(style) for style in styles)
    assert all(not style.bold for style in styles)


def test_quiet_annotation_reports_real_range_without_mutating_ohlc(penny_bounce, monkeypatch):
    chart = penny_bounce()
    before = chart.candles
    text = chart.render(120, height=45)
    assert "quiet" in text.plain.lower() or "small range" in text.plain.lower()
    assert "Quiet price" in lines(text)[0].plain
    assert "observed range 0.01" in text.plain.lower()
    assert "visible trades only" in text.plain.lower()
    assert "O 81,588.36" in text.plain and "L 81,588.35" in text.plain
    monkeypatch.setattr("time.time", lambda: START / 1000 + 86400)
    again = chart.render(120, height=45)
    assert (again.plain, again.spans) == (text.plain, text.spans)
    assert chart.candles == before
    assert {c.high for c in before} == {81588.36}
    assert {c.low for c in before} == {81588.35}


def test_flat_price_is_a_thin_mark_with_zero_observed_range():
    chart = new_chart()
    for bucket in range(120):
        chart.add_trade(81700, .1, True, START + bucket * 5000)
    text = chart.render(120, height=45)
    prices, _ = plot_sections(text)
    assert len(occupied_price_rows(text)) == 1
    assert not any(ch in BODY for row in prices for ch in row)
    assert "flat" in text.plain.lower()
    assert "observed range 0.00" in text.plain.lower()


@pytest.mark.parametrize("low,high", [(1.2345, 1.2346), (.00012345, .00012346),
                                      (1e-12, 1.0001e-12),
                                      (.000123450001, .000123450002),
                                      (1.234500001e-12, 1.234500002e-12)])
def test_small_price_pairs_remain_distinct_without_full_height_candles(low, high):
    chart = new_chart()
    for bucket in range(20):
        chart.add_trade(low, 1, False, START + bucket * 5000)
        chart.add_trade(high, 1, True, START + bucket * 5000 + 100)
    text = chart.render(120, height=45)
    occupied = occupied_price_rows(text)
    # A decimal tick can cover a few raster rows, but never the full pane.
    assert max(occupied) - min(occupied) + 1 <= 4
    labels = [float(label.replace(",", "")) for label in price_labels(text)]
    assert len(set(labels)) >= 3
    guide = next(row.plain.split("│", 1)[0].strip().lstrip("›") for row in lines(text)
                 if row.plain.lstrip().startswith("›"))
    assert float(guide.replace(",", "")) == high
    title_price = lines(text)[0].plain.rsplit("Last ", 1)[1]
    assert float(title_price.replace(",", "")) == high
    assert chart.candles[-1].open == low and chart.candles[-1].close == high


def test_normal_btc_movement_keeps_resolved_bodies_and_wicks():
    chart = new_chart()
    for bucket in range(12):
        open_price = 81588.35 + bucket * .75
        for trade, price in enumerate((open_price, open_price + .55,
                                       open_price - .30, open_price + .25)):
            chart.add_trade(price, .01, bool(trade % 2), START + bucket * 5000 + trade * 100)
    text = chart.render(120, height=45)
    prices, _ = plot_sections(text)
    assert len(occupied_price_rows(text)) >= len(prices) // 3
    assert any(ch in BODY for row in prices for ch in row)
    assert any("│" in row for row in prices)
    assert "quiet" not in text.plain.lower() and "flat" not in text.plain.lower()


def test_ohlcv_uses_exchange_order_not_arrival_order():
    chart = new_chart()
    for price, qty, buy, offset in [
        (102, 1, True, 3000),
        (100, 2, False, 1000),
        (101, 3, True, 4000),
        (104, 4, False, 2000),
    ]:
        chart.add_trade(price, qty, buy, START + offset)

    candle, = chart.candles
    assert candle.bucket_start_ms == START
    assert (candle.open, candle.high, candle.low, candle.close) == (100, 104, 100, 101)
    assert candle.volume == 10
    assert (candle.buy_volume, candle.sell_volume, candle.trades) == (4, 6, 4)
    assert (candle.first_time_ms, candle.last_time_ms) == (START + 1000, START + 4000)


def test_equal_timestamps_keep_first_open_last_close_and_all_volume():
    chart = new_chart()
    chart.add_trade(10, 2, True, START)
    chart.add_trade(12, 3, False, START)
    chart.add_trade(11, 4, True, START)
    candle, = chart.candles
    assert (candle.open, candle.high, candle.low, candle.close) == (10, 12, 10, 11)
    # No event ID exists in this API: same-time trades must not be deduplicated.
    assert candle.volume == 9
    assert candle.trades == 3


def test_bucket_boundaries_gaps_and_retained_late_events():
    chart = new_chart()
    chart.add_trade(100, 1, True, START + 4999)
    chart.add_trade(110, 2, True, START + 5000)
    chart.add_trade(120, 4, False, START + 20000)
    chart.add_trade(105, 3, False, START + 5500)
    assert [c.bucket_start_ms for c in chart.candles] == [START, START + 5000, START + 20000]
    assert chart.candles[1].close == 105
    assert chart.candles[1].volume == 5
    assert chart.candles[-1].close == 120
    assert sum(c.volume for c in chart.candles) == 10


def test_history_is_bounded_and_evicted_events_cannot_displace_recent_buckets():
    chart = new_chart(bucket_s=1)
    for i in range(750):
        chart.add_trade(100 + i, 1, True, START + i * 1000)
    assert len(chart.candles) <= 600
    assert chart.candles[-1].bucket_start_ms == START + 749000
    oldest = chart.candles[0].bucket_start_ms
    chart.add_trade(1, 999, False, START)
    chart.add_trade(900, 2, False, oldest + 500)
    assert chart.candles[0].bucket_start_ms == oldest
    assert chart.candles[0].volume == 3
    assert len(chart.candles) <= 600


def test_omitted_timestamp_is_the_only_wall_clock_fallback(monkeypatch):
    monkeypatch.setattr("time.time", lambda: START / 1000 + 3)
    chart = new_chart()
    chart.add_trade(100, 1, True)
    chart.add_trade(110, 2, False, START + 10000)
    assert [c.bucket_start_ms for c in chart.candles] == [START, START + 10000]


def test_shuffled_events_match_an_independent_sorted_trade_oracle():
    randomizer = random.Random(173)
    trades = [(START + randomizer.randrange(80_000), randomizer.randrange(900, 1100),
               randomizer.randrange(1, 10) / 8, bool(i % 2)) for i in range(1000)]
    randomizer.shuffle(trades)
    chart = new_chart()
    grouped = {}
    for sequence, (timestamp, price, qty, buy) in enumerate(trades):
        chart.add_trade(price, qty, buy, timestamp)
        grouped.setdefault(timestamp // 5000 * 5000, []).append(
            (timestamp, sequence, price, qty, buy)
        )
    assert len(chart.candles) == len(grouped)
    for candle in chart.candles:
        events = sorted(grouped[candle.bucket_start_ms])
        assert (candle.open, candle.close) == (events[0][2], events[-1][2])
        assert candle.high == max(event[2] for event in events)
        assert candle.low == min(event[2] for event in events)
        assert candle.volume == math.fsum(event[3] for event in events)
        assert candle.buy_volume == math.fsum(event[3] for event in events if event[4])
        assert candle.sell_volume == math.fsum(event[3] for event in events if not event[4])
    assert sum(c.volume for c in chart.candles) == math.fsum(trade[2] for trade in trades)


def test_rendering_during_silence_never_invents_candles_or_advances_time(monkeypatch):
    chart = new_chart()
    chart.add_trade(100, 1, True, START)
    before = chart.candles
    initial = chart.render(80, height=12).plain
    monkeypatch.setattr("time.time", lambda: START / 1000 + 86400)
    assert chart.render(80, height=12).plain == initial
    assert chart.candles == before
    # Retained snapshots must not change when the live current candle changes.
    chart.add_trade(110, 2, False, START + 1000)
    assert before[0].close == 100 and before[0].volume == 1


@pytest.mark.parametrize("bucket_s", [0, -1, math.nan, math.inf, -math.inf, True, "5s", 0.0001])
def test_rejects_invalid_timeframes(bucket_s):
    with pytest.raises(ValueError):
        new_chart(bucket_s=bucket_s)


@pytest.mark.parametrize("bucket_s,split_ms", [(0.5, 500), (1.001, 1001), (1.003, 1003)])
def test_fractional_second_timeframes_resolve_to_exchange_milliseconds(bucket_s, split_ms):
    chart = new_chart(bucket_s=bucket_s)
    chart.add_trade(1, 1, True, split_ms - 1)
    chart.add_trade(2, 1, True, split_ms)
    assert [c.bucket_start_ms for c in chart.candles] == [0, split_ms]


@pytest.mark.parametrize("field", ["price", "qty"])
@pytest.mark.parametrize("bad", [0, -1, math.nan, math.inf, -math.inf, True, None, "oops"])
def test_invalid_trades_are_rejected_without_mutating_history(field, bad):
    chart = new_chart()
    chart.add_trade(100, 2, True, START)
    before = chart.candles
    trade = {"price": 101, "qty": 3, "is_buy": True, "event_time": START + 1000}
    trade[field] = bad
    with pytest.raises(ValueError):
        chart.add_trade(**trade)
    assert chart.candles == before


@pytest.mark.parametrize("event_time", [-1, math.nan, math.inf, True, "yesterday", 10**30])
def test_rejects_invalid_exchange_times(event_time):
    chart = new_chart()
    with pytest.raises(ValueError):
        chart.add_trade(100, 1, True, event_time)
    assert not chart.candles


@pytest.mark.parametrize("mode", ["candle", "line"])
@pytest.mark.parametrize("size", [(80, 12), (40, 9), (20, 6), (9, 3), (1, 1), (0, 0)])
def test_render_obeys_cell_width_and_total_height_for_empty_and_populated_chart(mode, size):
    width, height = size
    chart = new_chart(mode=mode)
    for populated in (False, True):
        if populated:
            for i in range(40):
                chart.add_trade(0.000001 + i * 0.00000001, i + 1, i % 2 == 0, START + i * 5000)
        text = chart.render(max_cols=width, height=height, volume_label="基礎\nBTC\t[red]")
        assert isinstance(text, Text)
        assert len(text.plain.splitlines()) <= height
        assert all(cell_len(row) <= width for row in text.plain.splitlines())
        assert "\t" not in text.plain
        console = Console(file=StringIO(), width=max(1, width), force_terminal=True,
                          legacy_windows=False)
        console.print(text, end="")
        assert len(console.file.getvalue().splitlines()) <= height


def test_default_render_is_bounded_by_constructor_height():
    chart = new_chart(height=12)
    chart.add_trade(100, 1, True, START)
    text = chart.render(max_cols=80)
    assert len(text.plain.splitlines()) <= 12


@pytest.mark.parametrize("bad", [0, -1, 1.5, True, math.nan])
def test_rejects_invalid_constructor_heights(bad):
    with pytest.raises(ValueError):
        new_chart(height=bad)


def test_rejects_unknown_mode():
    with pytest.raises(ValueError):
        new_chart(mode="bars")


@pytest.mark.parametrize("kwargs", [{"height": -1}, {"height": 1.5}, {"max_cols": -1},
                                    {"max_cols": math.inf}, {"max_cols": True}])
def test_rejects_invalid_render_dimensions(kwargs):
    with pytest.raises(ValueError):
        new_chart().render(**kwargs)


def test_overflowing_volume_is_rejected_atomically():
    chart = new_chart()
    chart.add_trade(100, 1e308, True, START)
    before = chart.candles
    with pytest.raises(ValueError):
        chart.add_trade(200, 1e308, False, START + 1000)
    assert chart.candles == before


def test_candles_have_distinct_bodies_wicks_dojis_and_direction_colors():
    chart = new_chart()
    for price, offset in [(100, 0), (110, 1000), (90, 2000), (105, 3000)]:
        chart.add_trade(price, 1, True, START + offset)
    for price, offset in [(105, 5000), (95, 6000)]:
        chart.add_trade(price, 1, False, START + offset)
    chart.add_trade(102, 1, True, START + 10000)
    text = chart.render(80, height=18)
    price_rows, _ = plot_sections(text)
    assert any(ch in BODY for row in price_rows for ch in row)
    assert any("│" in row for row in price_rows)  # wick, excluding the price axis
    assert any("──" in row for row in price_rows)  # single-price doji, not a full block
    price_pane_end = text.plain.index("\nVol (")
    body_styles = [str(text.get_style_at_offset(Console(), i))
                   for i, ch in enumerate(text.plain[:price_pane_end]) if ch in BODY]
    assert any("green" in style for style in body_styles)
    assert any("red" in style for style in body_styles)


def test_time_gaps_are_blank_in_both_price_and_aligned_real_volume():
    chart = new_chart()
    chart.add_trade(100, 1, True, START)
    chart.add_trade(100, 8, True, START + 15000)
    text = chart.render(80, height=16, volume_label="BTC")
    price_rows, volume_rows = plot_sections(text)
    doji_starts = [i for i in range(len(price_rows[0]) - 1)
                  if any(row[i:i + 2] == "──" for row in price_rows)]
    assert len(doji_starts) == 2
    left, right = doji_starts
    assert right - left == 9  # three 5-second slots, not two adjacent invented bars
    for x in (left + 3, left + 6):
        assert not any(row[x] in BODY or row[x] == "│" for row in price_rows)
        assert all(row[x:x + 2] == "  " for row in volume_rows)
    left_volume = sum(ch in VOLUME for row in volume_rows for ch in row[left:left + 2])
    right_volume = sum(ch in VOLUME for row in volume_rows for ch in row[right:right + 2])
    assert 0 < left_volume < right_volume
    assert "Vol (BTC)" in text.plain
    assert "8" in text.plain


def test_utc_times_last_price_guide_and_true_latest_ohlc_are_visible():
    chart = new_chart(bucket_s=60)
    chart.add_trade(100, 2, True, START)
    chart.add_trade(110, 3, True, START + 60000)
    chart.add_trade(105, 4, False, START + 61000)
    chart.add_trade(999, 1, False, START + 1000)  # late old candle is not the last price
    text = chart.render(100, height=16, volume_label="BTC")
    assert "UTC" in text.plain
    assert "00:00" in text.plain and "00:01" in text.plain
    assert "23:59" not in text.plain and "00:02" not in text.plain
    assert "Last 105" in text.plain
    assert "O 110" in text.plain and "H 110" in text.plain
    assert "L 105" in text.plain and "C 105" in text.plain
    assert "┄" in text.plain  # last-price guide separate from the candle body


def test_narrow_normal_plot_keeps_complete_last_price_instead_of_cutting_a_number():
    chart = new_chart()
    chart.add_trade(105, 1, True, START)
    assert "Last 105.00" in lines(chart.render(40, height=9))[0].plain


@pytest.mark.parametrize("close", [90.5, 91, 93, 95, 97, 99, 101, 103, 105, 107, 109, 109.5])
def test_last_price_guide_uses_the_same_subcell_mapping_as_the_line_endpoint(close):
    chart = new_chart(mode="line")
    for i, price in enumerate((90, 110, close)):
        chart.add_trade(price, 1, True, START + i * 5000)
    price_rows, _ = plot_sections(chart.render(80, height=16))
    guide_row = next(i for i, row in enumerate(price_rows) if "┄" in row)
    last_x = max(x for row in price_rows for x, ch in enumerate(row)
                 if "\u2801" <= ch <= "\u28ff")
    assert "\u2801" <= price_rows[guide_row][last_x] <= "\u28ff"


def test_line_mode_connects_adjacent_closes_but_does_not_bridge_missing_buckets():
    chart = new_chart(mode="line")
    for price, offset in [(100, 0), (110, 5000), (90, 10000), (105, 25000)]:
        chart.add_trade(price, 1, True, START + offset)
    price_rows, volume_rows = plot_sections(chart.render(80, height=16))
    occupied = sorted({x for row in price_rows for x, ch in enumerate(row)
                       if "\u2801" <= ch <= "\u28ff"})
    assert occupied
    # The first three adjacent points occupy every intervening terminal column.
    first_run = occupied[:next((i for i in range(1, len(occupied))
                               if occupied[i] != occupied[i - 1] + 1), len(occupied))]
    assert len(first_run) >= 5
    assert occupied[-1] - first_run[-1] >= 5  # actual gap before final isolated observation
    assert any(ch in VOLUME for row in volume_rows for ch in row)


@pytest.mark.parametrize("price", [5e-324, 1e-12, 50000.12345, 1e300, 1.7976931348623157e308])
def test_finite_extreme_and_flat_prices_render_without_invalid_scales(price):
    chart = new_chart()
    chart.add_trade(price, 1, True, START)
    text = chart.render(80, height=12)
    assert "nan" not in text.plain.lower()
    assert "inf" not in text.plain.lower()
    assert all(cell_len(row) <= 80 for row in text.plain.splitlines())


def test_outlier_volume_uses_explicit_sqrt_scale_without_changing_quantities():
    chart = new_chart()
    for bucket in range(20):
        chart.add_trade(81588.35, 1.27706 if bucket == 0 else .0029,
                        bool(bucket % 2), START + bucket * 5000)
    before = chart.candles
    text = chart.render(100, height=36, volume_label="BTC")
    assert "sqrt" in text.plain
    _, volumes = plot_sections(text)
    heights = [sum(VOLUME_GLYPHS.index(row[x]) if row[x] in VOLUME_GLYPHS else 0
                   for row in volumes) for x in range(len(volumes[0]))]
    positive = [height for height in heights if height]
    assert min(positive) >= 2  # Small real trades are not all forced to one pixel.
    assert max(positive) > min(positive) * 4  # The true large spike stays dominant.
    assert chart.candles == before
    assert chart.candles[-1].volume == .0029


VOLUME_GLYPHS = " ▁▂▃▄▅▆▇█"


def test_doji_volume_has_actual_taker_side_tint_instead_of_yellow_price_noise():
    chart = new_chart()
    chart.add_trade(81588.35, 1, True, START)
    chart.add_trade(81588.35, 2, False, START + 5000)
    text = chart.render(100, height=24)
    assert "linear" in text.plain
    assert "taker tint" in text.plain
    offset = text.plain.index("\nVol (")
    styles = [str(text.get_style_at_offset(Console(), i))
              for i, char in enumerate(text.plain) if i > offset and char in VOLUME]
    assert any("green" in style for style in styles)
    assert any("red" in style for style in styles)


@pytest.mark.parametrize("width", [24, 28, 32, 40])
def test_volume_scale_label_survives_narrow_plot_with_long_asset_name(width):
    chart = new_chart()
    for index, qty in enumerate((1, 100, 1)):
        chart.add_trade(81588.35, qty, True, START + index * 5000)
    text = chart.render(width, height=24, volume_label="ABCDEFGHIJKLMNOP")
    heading = next(row for row in text.plain.splitlines() if row.startswith("Vol ("))
    assert "sqrt" in heading
    assert cell_len(heading) <= width
