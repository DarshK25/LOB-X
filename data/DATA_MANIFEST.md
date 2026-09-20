# LOB-X Data Manifest

Last updated: 2026-09-15

## Curated Binance Vision Datasets

These files were downloaded from official Binance Vision futures USD-M daily
`aggTrades` archives:

`https://data.binance.vision/data/futures/um/daily/aggTrades/BTCUSDT/`

| File | Rows | Size | Time Range UTC | Price Range | BTC Volume | SHA256 |
| --- | ---: | ---: | --- | ---: | ---: | --- |
| `data/parquet/BTCUSDT-futures-um-aggTrades-2024-08-01_to_2024-08-14.parquet` | 27,361,045 | 611.22 MB | 2024-08-01 to 2024-08-14 | 48,888.0-65,650.0 | 5,535,288.116 | `AB9EC8942C10D20C4B6B1478489CB804B87C6FB68086A2EC9C5993F38CBEFE04` |
| `data/parquet/BTCUSDT-futures-um-aggTrades-2026-09-01_to_2026-09-14.parquet` | 14,845,226 | 323.20 MB | 2026-09-01 to 2026-09-14 | 76,000.3-82,282.8 | 1,838,496.410 | `368A4EE17055A22EFD84A1865D28007EFC4AB17924E137B8F5E1A4DDB46E1C5F` |

Total curated Binance Vision size: about 934 MB.

## How To Use

Large synthetic-depth replay smoke test:

```bash
python scripts/run_backtest.py --binance-parquet data/parquet/BTCUSDT-futures-um-aggTrades-2026-09-01_to_2026-09-14.parquet --max-trades 100000 --strategy as --scenario conservative
```

Research validation on a capped subset:

```bash
python scripts/run_quant_validation.py --parquet data/parquet/BTCUSDT-futures-um-aggTrades-2026-09-01_to_2026-09-14.parquet --max-trades 50000
```

Download more data, staying mindful of disk budget:

```bash
python scripts/download_binance_vision.py --symbol BTCUSDT --market futures-um --data-type aggTrades --start-date 2026-09-01 --end-date 2026-09-14
```

## Limitations

`aggTrades` are aggressive trade archives. They are excellent for studying trade
flow, volatility, regimes, and coarse strategy stress tests, but they are not
true L2 depth snapshots and cannot prove queue priority.

For stronger market-making evidence, use collected files under `data/raw`:

- `depth_YYYY-MM-DD.parquet`
- `trades_YYYY-MM-DD.parquet`

Those captures include the local L2 depth state and can drive the stricter
historical replay path.

## Cleanup Candidates

These files are redundant after the curated two-week datasets above:

- `data/parquet/BTCUSDT-futures-um-aggTrades-2024-08-01_to_2024-08-01.parquet`
- `data/parquet/BTCUSDT-futures-um-aggTrades-2026-09-14_to_2026-09-14.parquet`
- `data/parquet/BTCUSDT-aggTrades-2024-08-01_to_2024-08-01.parquet`

Do not delete raw L2 captures until a separate raw-data manifest is created.

