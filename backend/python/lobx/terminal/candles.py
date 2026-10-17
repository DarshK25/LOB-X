"""Bounded exchange-time OHLCV and a cell-budgeted Rich live chart.

Only received trades make candles. Rendering neither samples the clock nor fills
quiet intervals. All quantities (including the histogram) are in base-asset units.
"""
from __future__ import annotations

import math
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from statistics import median

from rich.text import Text

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
_MAX_TIME_MS = 253_402_300_799_999  # Last millisecond representable by datetime.
_VOLUME_BLOCKS = " ▁▂▃▄▅▆▇█"
_Cell = tuple[str, str]


@dataclass(frozen=True, slots=True)
class Candle:
    """Immutable public OHLCV record; timestamps are exchange milliseconds.

    Equal event times use arrival order as the only available tie-breaker: first
    arrival opens, last arrival closes. Without trade IDs duplicates cannot be
    identified, so every accepted call contributes its quantity exactly once.
    """

    bucket_start_ms: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    buy_volume: float
    sell_volume: float
    trades: int
    first_time_ms: float
    last_time_ms: float


def _number(value: float, name: str, *, allow_zero: bool = False) -> float:
    if isinstance(value, (bool, str, bytes)):
        raise ValueError(f"{name} must be a finite number")  # noqa: TRY004 - unified input contract.
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{name} must be a finite number") from exc
    if not math.isfinite(number) or number < 0 or (number == 0 and not allow_zero):
        raise ValueError(f"{name} must be finite and {'nonnegative' if allow_zero else 'positive'}")
    return number


def _dimension(value: int, name: str, *, allow_zero: bool = True) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < int(not allow_zero):
        raise ValueError(f"{name} must be a {'nonnegative' if allow_zero else 'positive'} integer")
    return value


def _utc(timestamp_ms: int) -> datetime:
    # Epoch arithmetic works for early dates on Windows, unlike fromtimestamp().
    return _EPOCH + timedelta(milliseconds=timestamp_ms)


def _price(value: float, step: float | None = None) -> str:
    if value and (value < 0.0001 or value >= 1e8):
        precision = (max(1, min(15, 1 + math.floor(math.log10(value))
                               - math.floor(math.log10(step))))
                     if step is not None and step > 0 else 8)
        return f"{value:.{precision}g}"
    if step is None:
        decimals = max(2, min(10, 7 - math.floor(math.log10(value)))) if value else 2
    else:
        decimals = max(2, min(15, -math.floor(math.log10(step)))) if step > 0 else 6
    text = f"{value:,.{decimals}f}"
    if step is None:
        integer, fraction = text.split(".")
        return integer + "." + fraction.rstrip("0").ljust(2, "0")
    return text


def _axis_price(value: float, step: float, width: int) -> str:
    """A complete number, never an axis value with its significant suffix cut off."""
    label = _price(value, step)
    if len(label) <= width:
        return label
    for precision in range(max(1, width - 6), -1, -1):
        label = f"{value:.{precision}e}"
        if len(label) <= width:
            return label
    return "—"


def _inferred_tick(candles: list[Candle]) -> float:
    """Estimate decimal precision, not an exchange's guaranteed minimum tick.

    Use real OHLC prices (including subcent changes), with a cent fallback for
    integer prices. Fifteen significant digits discard float arithmetic fuzz;
    inference is local to the visible data and never changes collector state.
    """
    exponent = min(-2, *(int(Decimal(f"{price:.15g}").normalize().as_tuple().exponent)
                         for candle in candles
                         for price in (candle.open, candle.high, candle.low, candle.close)))
    return max(math.ulp(0.0), 10.0 ** exponent)


def _direction(candle: Candle) -> str:
    return "green" if candle.close > candle.open else "red" if candle.close < candle.open else "yellow"


def _bounded(rows: list[Text], width: int, height: int) -> Text:
    out = Text(no_wrap=True, overflow="crop", end="")
    if not width or not height:
        return out
    for index, row in enumerate(rows[:height]):
        if index:
            out.append("\n")
        row.truncate(width, overflow="crop")  # Rich measures terminal cells, not code points.
        out.append(row)
    return out


class AdaptiveTerminalChart:
    """Real candles/connected close line and aligned base-volume histogram.

    ``height`` is the TOTAL row budget, including headings, volume and UTC axis.
    ``bucket_s`` must resolve to whole positive milliseconds. History keeps at
    most 600 actual buckets, not individual trades. Late retained events update
    their own bucket; events older than a full retained history are ignored.
    This mutable collector is intended for the CLI's single event-loop owner.

    Price scaling has a full-span floor of 16 inferred decimal ticks or 0.05 bp
    (0.0005%) of the latest price, whichever is larger. Wider observed ranges get
    8% padding per side. Bounds round outward to clean steps where float-safe.
    E.g. a cent tick at 81,588.36 has a 0.4079418 minimum span, not dollar padding.
    """

    MAX_BUCKETS = 600

    def __init__(self, height: int = 12, bucket_s: float = 5, mode: str = "candle") -> None:
        self.height = _dimension(height, "height", allow_zero=False)
        seconds = _number(bucket_s, "bucket_s")
        milliseconds = seconds * 1000
        if (not math.isfinite(milliseconds) or not 1 <= milliseconds <= _MAX_TIME_MS
                or not math.isclose(milliseconds, round(milliseconds), rel_tol=0, abs_tol=1e-7)):
            raise ValueError("bucket_s must resolve to whole positive milliseconds")
        if mode not in ("candle", "line"):
            raise ValueError("mode must be 'candle' or 'line'")
        self.bucket_s = seconds
        self.mode = mode
        # Decimal second inputs such as 1.001 can multiply to 1000.9999999999.
        self._bucket_ms = round(milliseconds)
        self._candles: dict[int, Candle] = {}

    @property
    def candles(self) -> tuple[Candle, ...]:
        """Chronological immutable snapshot of retained actual OHLCV buckets."""
        return tuple(self._candles[key] for key in sorted(self._candles))

    def add_trade(
        self, price: float, qty: float, is_buy: bool, event_time: float | None = None,
    ) -> None:
        """Accumulate an exchange-millisecond event; only None uses local time.

        Invalid prices/quantities/times or overflowing volume raise ValueError
        before mutation. Positive fractional-millisecond times retain their
        event ordering; bucket edges themselves are whole milliseconds.
        """
        price = _number(price, "price")
        qty = _number(qty, "qty")
        if not isinstance(is_buy, bool):
            raise ValueError("is_buy must be a boolean")  # noqa: TRY004 - unified input contract.
        timestamp = _number(
            time.time() * 1000 if event_time is None else event_time,
            "event_time", allow_zero=True,
        )
        if timestamp > _MAX_TIME_MS:
            raise ValueError("event_time is outside the UTC calendar range")
        bucket = int(timestamp) // self._bucket_ms * self._bucket_ms
        current = self._candles.get(bucket)
        if current is None:
            if len(self._candles) >= self.MAX_BUCKETS and bucket < min(self._candles):
                return
            candle = Candle(bucket, price, price, price, price, qty,
                            qty if is_buy else 0.0, 0.0 if is_buy else qty, 1,
                            timestamp, timestamp)
        else:
            volume = current.volume + qty
            if not math.isfinite(volume):
                raise ValueError("aggregate volume exceeds finite range")
            candle = replace(
                current,
                open=price if timestamp < current.first_time_ms else current.open,
                high=max(current.high, price), low=min(current.low, price),
                close=price if timestamp >= current.last_time_ms else current.close,
                volume=volume,
                buy_volume=current.buy_volume + (qty if is_buy else 0),
                sell_volume=current.sell_volume + (0 if is_buy else qty),
                trades=current.trades + 1,
                first_time_ms=min(current.first_time_ms, timestamp),
                last_time_ms=max(current.last_time_ms, timestamp),
            )
        self._candles[bucket] = candle
        if len(self._candles) > self.MAX_BUCKETS:
            del self._candles[min(self._candles)]

    def render(
        self, max_cols: int = 80, height: int | None = None, volume_label: str = "base",
    ) -> Text:
        """Return Text within the exact cell/row budgets, even for empty/tiny views.

        Normal views reserve price, volume and time panes. Below 24 columns or
        eight rows a bounded last-price/volume summary replaces an illegible plot.
        The time window ends at the last received bucket, never at the wall clock;
        old off-screen candles are not compressed across real quiet intervals.
        """
        width = _dimension(max_cols, "max_cols")
        height = self.height if height is None else _dimension(height, "height")
        if not width or not height:
            return _bounded([], width, height)
        candles = self.candles
        if not candles:
            return _bounded([Text("Waiting for live trades…", style="dim cyan")], width, height)

        # Caller-supplied asset names are plain text, never markup/control codes.
        asset = "".join(ch for ch in str(volume_label) if ch.isprintable()) or "base"
        asset_text = Text(asset)
        asset_text.truncate(16, overflow="crop")
        asset = asset_text.plain
        last = candles[-1]
        last_price = _price(last.close, _inferred_tick([last]))
        title = f"{self.mode.upper()} {self.bucket_s:g}s · Last {last_price}"
        vol_title = f"Vol ({asset}) · last {last.volume:.6g}"
        if width < 24 or height < 8:
            return _bounded([
                Text(title, style="bold " + _direction(last)),
                Text(vol_title, style="dim"),
                Text(_utc(last.bucket_start_ms).strftime("%Y-%m-%d %H:%M:%S UTC"), style="dim"),
            ], width, height)

        volume_height = max(1, min(4, (height - 4) // 4))
        price_height = height - volume_height - 5  # Include the observed-range annotation.
        # A stable, modest gutter avoids resizes jumping between differently
        # sized windows. Oversized/small prices use scientific notation.
        gutter = min(18, max(9, len(last_price) + 3))
        stride = 3 if self.mode == "candle" else 2
        capacity = max(1, (width - gutter - 1) // stride)
        plot_width = capacity * stride
        start = last.bucket_start_ms - (capacity - 1) * self._bucket_ms
        visible = [c for c in candles if c.bucket_start_ms >= start]
        columns = [(c, (c.bucket_start_ms - start) // self._bucket_ms * stride) for c in visible]

        # Scale in normalized price space: finite extreme prices must not create
        # infinite padding, a zero denominator, or a runaway tick-label loop.
        highs = [c.high if self.mode == "candle" else c.close for c in visible]
        lows = [c.low if self.mode == "candle" else c.close for c in visible]
        scale = max(highs)
        low = min(lows) / scale
        tick = _inferred_tick(visible)
        # This is a minimum FULL span, not just padding around a penny range.
        # Normalize before multiplying to avoid overflow at finite price extremes.
        minimum_span = max(16 * (tick / scale), (last.close / scale) * 5e-6,
                           16 * math.ulp(1.0))
        full_span = max((1 - low) * 1.16, minimum_span)
        maximum = sys.float_info.max / scale
        center = low + (1 - low) / 2
        bottom = max(0.0, min(center - full_span / 2, maximum - full_span))
        top = min(maximum, bottom + full_span)

        # Round outward to 1/2/5 steps about a tenth of the span, never finer
        # than the inferred tick. Skip snapping if subnormal/float precision
        # would make division unsafe. There is no scale history or render mutation.
        raw_step = full_span / 10 * scale
        magnitude = 10.0 ** math.floor(math.log10(raw_step)) if raw_step else 0.0
        if magnitude:
            ratio = raw_step / magnitude
            clean_step = magnitude * (5 if ratio >= 5 else 2 if ratio >= 2 else 1)
            quantum = max(tick, clean_step) / scale
            if 1e-14 <= quantum <= 1:
                bottom = min(bottom, math.floor(bottom / quantum) * quantum)
                top = max(top, min(maximum, math.ceil(top / quantum) * quantum))
        span = top - bottom

        def position(price: float, levels: int) -> int:
            fraction = (top - price / scale) / span
            return max(0, min(levels - 1, round(fraction * (levels - 1))))

        resolution = 2 if self.mode == "candle" else 4
        guide_row = position(last.close, price_height * resolution) // resolution
        grid: list[list[_Cell]] = [[(" ", "") for _ in range(plot_width)]
                                   for _ in range(price_height)]
        label_rows = {0, price_height // 2, price_height - 1}
        for row in range(price_height):
            for x in range(plot_width):
                if row == guide_row:
                    grid[row][x] = ("┄", "dim " + _direction(last))
                elif row in label_rows and x % 3 == 0:
                    grid[row][x] = ("·", "dim bright_black")

        if self.mode == "candle":
            self._draw_candles(grid, columns, position)
        else:
            self._draw_line(grid, columns, position)

        first_time = _utc(visible[0].bucket_start_ms)
        last_time = _utc(last.bucket_start_ms)
        clock_range = f"{first_time:%H:%M:%S}–{last_time:%H:%M:%S}"
        observed_range = max(c.high for c in visible) - min(c.low for c in visible)
        context = ("Flat price · " if observed_range == 0 else
                   "Quiet price · " if observed_range / scale <= minimum_span else "")
        heading = Text(f"{context}{self.mode.upper()} {self.bucket_s:g}s · UTC {clock_range} · Last {last_price}")
        if heading.cell_len > width:
            heading = Text(f"{context}{title} · UTC")
        if heading.cell_len > width:
            heading = Text(f"Last {last_price} · UTC")
        heading.stylize("bold cyan")
        rows = [heading]
        for row, cells in enumerate(grid):
            if row == guide_row:
                label, style = "›" + _axis_price(last.close, tick, gutter - 1), "bold " + _direction(last)
            elif row in label_rows:
                price = min(sys.float_info.max,
                            (top - row / max(1, price_height - 1) * span) * scale)
                label, style = _axis_price(price, tick, gutter), "dim"
            else:
                label, style = "", "dim"
            axis = Text(label)
            axis.truncate(gutter, overflow="crop")
            axis.align("right", gutter)
            axis.stylize(style)
            axis.append("│", style="dim bright_black")
            for char, color in cells:
                axis.append(char, style=color)
            rows.append(axis)

        max_volume = max(c.volume for c in visible)
        # Keep small observed buckets readable beside a single large print.
        # Nonlinear geometry is always explicit; displayed quantities stay raw.
        sqrt_volume = max_volume / median(c.volume for c in visible) > 16
        volume_scale = "sqrt" if sqrt_volume else "linear"
        legend_asset = Text(asset)
        legend_asset.truncate(max(0, width - len(f"Vol () · {volume_scale}")), overflow="crop")
        rows.append(Text(
            f"Vol ({legend_asset.plain}) · {volume_scale} · taker tint · max {max_volume:.6g} · last {last.volume:.6g}",
            style="dim",
        ))
        volume_grid: list[list[_Cell]] = [[(" ", "") for _ in range(plot_width)]
                                          for _ in range(volume_height)]
        for candle, x in columns:
            fraction = candle.volume / max_volume
            if sqrt_volume:
                fraction = math.sqrt(fraction)
            levels = max(1, round(fraction * volume_height * 8))
            color = ("green" if candle.buy_volume > candle.sell_volume else
                     "red" if candle.sell_volume > candle.buy_volume else "white")
            for row in range(volume_height):
                fill = max(0, min(8, levels - (volume_height - 1 - row) * 8))
                if fill:
                    for col in (x, x + 1):
                        volume_grid[row][col] = (_VOLUME_BLOCKS[fill], color)
        for cells in volume_grid:
            volume_row = Text(" " * gutter + "│", style="dim bright_black")
            for char, color in cells:
                volume_row.append(char, style=color)
            rows.append(volume_row)
        rows.append(self._time_axis(columns, gutter, plot_width))
        rows.append(Text(
            f"O {_price(last.open, tick)}  H {_price(last.high, tick)}  "
            f"L {_price(last.low, tick)}  C {_price(last.close, tick)}",
            style="dim " + _direction(last),
        ))
        rows.append(Text(
            f"{context}Observed range {_price(observed_range, tick)} · visible trades only",
            style="dim",
        ))
        return _bounded(rows, width, height)

    @staticmethod
    def _draw_candles(
        grid: list[list[_Cell]], columns: list[tuple[Candle, int]],
        position: Callable[[float, int], int],
    ) -> None:
        levels = len(grid) * 2
        for candle, x in columns:
            color = _direction(candle)
            high = position(candle.high, levels)
            low = position(candle.low, levels)
            for row in range(high // 2, low // 2 + 1):
                grid[row][x + 1] = ("│", "dim " + color)
            top = position(max(candle.open, candle.close), levels)
            bottom = position(min(candle.open, candle.close), levels)
            # A sub-row body is a thin directional mark, not an inflated block.
            # True dojis remain neutral yellow; their real wicks are still drawn.
            if candle.open == candle.close or top // 2 == bottom // 2:
                for col in (x, x + 1):
                    grid[top // 2][col] = ("─", color)
                continue
            for row in range(top // 2, bottom // 2 + 1):
                upper = top <= row * 2 <= bottom
                lower = top <= row * 2 + 1 <= bottom
                glyph = "█" if upper and lower else "▀" if upper else "▄"
                for col in (x, x + 1):
                    grid[row][col] = (glyph, "bold " + color)

    def _draw_line(
        self, grid: list[list[_Cell]], columns: list[tuple[Candle, int]],
        position: Callable[[float, int], int],
    ) -> None:
        # A 2x4 braille raster gives a connected path instead of one block per
        # close. Bresenham joins only adjacent real buckets, never silent gaps.
        dots: dict[tuple[int, int], tuple[int, str]] = {}
        bits = ((1, 2, 4, 64), (8, 16, 32, 128))

        def segment(x0: int, y0: int, x1: int, y1: int, color: str) -> None:
            dx, dy = abs(x1 - x0), -abs(y1 - y0)
            sx, sy = (1 if x0 < x1 else -1), (1 if y0 < y1 else -1)
            error = dx + dy
            while True:
                key = (y0 // 4, x0 // 2)
                mask = dots.get(key, (0, color))[0] | bits[x0 % 2][y0 % 4]
                dots[key] = (mask, color)
                if x0 == x1 and y0 == y1:
                    break
                twice = error * 2
                if twice >= dy:
                    error += dy
                    x0 += sx
                if twice <= dx:
                    error += dx
                    y0 += sy

        previous: tuple[Candle, int] | None = None
        for candle, col in columns:
            x, y = col * 2 + 1, position(candle.close, len(grid) * 4)
            if previous and candle.bucket_start_ms - previous[0].bucket_start_ms == self._bucket_ms:
                prior, prior_x = previous
                color = "green" if candle.close >= prior.close else "red"
                segment(prior_x * 2 + 1, position(prior.close, len(grid) * 4), x, y, color)
            else:
                segment(x, y, x, y, _direction(candle))
            previous = (candle, col)
        for (row, col), (mask, color) in dots.items():
            grid[row][col] = (chr(0x2800 + mask), "bold " + color)

    def _time_axis(self, columns: list[tuple[Candle, int]], gutter: int, width: int) -> Text:
        cells = [" "] * width
        occupied: set[int] = set()
        candidates = [columns[-1], columns[0], columns[len(columns) // 2]]
        across_days = _utc(columns[0][0].bucket_start_ms).date() != _utc(
            columns[-1][0].bucket_start_ms
        ).date()
        for candle, col in candidates:
            date = _utc(candle.bucket_start_ms)
            if across_days:
                label = date.strftime("%m-%d %H:%M")
            elif self._bucket_ms >= 60_000:
                label = date.strftime("%H:%M")
            else:
                label = date.strftime("%H:%M:%S")
                if self._bucket_ms < 1000:
                    label += f".{candle.bucket_start_ms % 1000:03d}"
            if len(label) > width:
                continue
            start = max(0, min(width - len(label), col - len(label) // 2))
            positions = set(range(start, start + len(label)))
            if occupied.intersection(positions):
                continue
            cells[start:start + len(label)] = label
            occupied.update(range(max(0, start - 1), min(width, start + len(label) + 1)))
        return Text(" " * gutter + "└" + "".join(cells), style="dim")
