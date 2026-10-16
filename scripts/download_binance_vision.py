"""Binance Vision archive downloader and Parquet converter.

This downloads public Binance Vision trade-style archives and converts them to
Parquet for LOB-X research. Public Vision aggTrades/trades are useful for large
trade-flow studies, but they are not true Level-3 queue data and are not enough
to prove production HFT profitability by themselves.
"""
from __future__ import annotations

import argparse
import io
import sys
import urllib.request
import zipfile
from pathlib import Path

import pandas as pd

DEFAULT_START = "2024-08-01"


def _date_range(start: str, end: str | None = None, days: int | None = None) -> list[str]:
    start_ts = pd.Timestamp(start).date()
    if end is not None:
        end_ts = pd.Timestamp(end).date()
        if end_ts < start_ts:
            raise ValueError("--end-date must be on or after --start-date")
        count = (end_ts - start_ts).days + 1
    else:
        count = days or 1
    return [
        (pd.Timestamp(start_ts) + pd.Timedelta(days=i)).strftime("%Y-%m-%d")
        for i in range(count)
    ]


def _base_prefix(market: str) -> str:
    if market == "spot":
        return "data/spot"
    if market == "futures-um":
        return "data/futures/um"
    if market == "futures-cm":
        return "data/futures/cm"
    raise ValueError(f"unsupported market: {market}")


def _normalise_archive_frame(df: pd.DataFrame, *, data_type: str, symbol: str) -> pd.DataFrame:
    """Return a canonical trade-like DataFrame from Binance Vision CSV data."""
    if data_type not in {"aggTrades", "trades"}:
        raise ValueError("LOB-X parquet conversion currently supports aggTrades/trades")

    df = df.rename(
        columns={
            "quantity": "qty",
            "timestamp": "transact_time",
            "time": "transact_time",
            "isBuyerMaker": "is_buyer_maker",
            "isBestMatch": "is_best_match",
        }
    )

    if data_type == "aggTrades" and "transact_time" not in df.columns:
        df.columns = [
            "agg_trade_id",
            "price",
            "qty",
            "first_trade_id",
            "last_trade_id",
            "transact_time",
            "is_buyer_maker",
        ][: len(df.columns)]
    elif data_type == "trades" and "transact_time" not in df.columns:
        df.columns = [
            "trade_id",
            "price",
            "qty",
            "quote_qty",
            "transact_time",
            "is_buyer_maker",
            "is_best_match",
        ][: len(df.columns)]

    if "agg_trade_id" not in df.columns and "trade_id" in df.columns:
        df["agg_trade_id"] = df["trade_id"]
    if "trade_id" not in df.columns and "agg_trade_id" in df.columns:
        df["trade_id"] = df["agg_trade_id"]
    if "symbol" not in df.columns:
        df["symbol"] = symbol

    required = {"price", "qty", "transact_time", "is_buyer_maker"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"archive missing required columns: {sorted(missing)}")

    df["price"] = pd.to_numeric(df["price"], errors="coerce")
    df["qty"] = pd.to_numeric(df["qty"], errors="coerce")
    df["transact_time"] = pd.to_numeric(df["transact_time"], errors="coerce")
    df["is_buyer_maker"] = df["is_buyer_maker"].astype(bool)
    df = df.dropna(subset=["price", "qty", "transact_time"]).copy()
    df["transact_time"] = df["transact_time"].astype("int64")
    return df


def download_and_convert(
    symbol: str = "BTCUSDT",
    dates: list[str] | None = None,
    output_dir: str | Path = "data/parquet",
    market: str = "futures-um",
    data_type: str = "aggTrades",
) -> Path:
    if dates is None:
        dates = _date_range(DEFAULT_START, days=7)

    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    raw_frames = []
    prefix = _base_prefix(market)
    print(
        f"Downloading Binance Vision {market}/{data_type} for {symbol} ({len(dates)} days)...",
        flush=True,
    )

    for day in dates:
        url = (
            f"https://data.binance.vision/{prefix}/daily/{data_type}/"
            f"{symbol}/{symbol}-{data_type}-{day}.zip"
        )
        print(f"  Fetching {day} -> {url}", flush=True)
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(request) as response:
                content = response.read()
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                csv_filename = archive.namelist()[0]
                with archive.open(csv_filename) as file:
                    frame = pd.read_csv(file)
                    frame = _normalise_archive_frame(
                        frame, data_type=data_type, symbol=symbol
                    )
                    raw_frames.append(frame)
                    print(f"    Loaded {len(frame):,} rows for {day}", flush=True)
        except Exception as exc:
            print(f"  [Warning] Failed to download/process {day}: {exc}", flush=True)

    if not raw_frames:
        raise RuntimeError("No historical data files were successfully downloaded.")

    combined = pd.concat(raw_frames, ignore_index=True)
    combined.sort_values("transact_time", inplace=True)
    combined.reset_index(drop=True, inplace=True)

    filename = f"{symbol}-{market}-{data_type}-{dates[0]}_to_{dates[-1]}.parquet"
    target_parquet = out_path / filename
    combined.to_parquet(target_parquet, engine="pyarrow", index=False)

    print(f"\nSuccessfully saved {len(combined):,} rows to:", flush=True)
    print(f"  {target_parquet.resolve()}", flush=True)
    return target_parquet


def main() -> None:
    parser = argparse.ArgumentParser(description="Download and convert Binance Vision data")
    parser.add_argument("--symbol", default="BTCUSDT", help="Symbol ticker")
    parser.add_argument(
        "--market",
        choices=["spot", "futures-um", "futures-cm"],
        default="futures-um",
    )
    parser.add_argument("--data-type", choices=["aggTrades", "trades"], default="aggTrades")
    parser.add_argument("--start-date", default=DEFAULT_START, help="First UTC date")
    parser.add_argument("--end-date", default=None, help="Last UTC date, inclusive")
    parser.add_argument("--days", type=int, default=None, help="Number of days from start date")
    parser.add_argument("--output-dir", default="data/parquet", help="Parquet target directory")
    args = parser.parse_args()

    dates = _date_range(args.start_date, end=args.end_date, days=args.days)
    download_and_convert(
        symbol=args.symbol,
        dates=dates,
        output_dir=args.output_dir,
        market=args.market,
        data_type=args.data_type,
    )


if __name__ == "__main__":
    if sys.platform == "win32":
        # Keep Windows terminal output stable when run from older consoles.
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass
    main()
