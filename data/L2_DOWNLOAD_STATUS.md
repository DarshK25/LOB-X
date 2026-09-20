# L2 Data Download in Progress

**Status:** ✅ DOWNLOADING  
**Source:** CryptoHFTData (binance_futures)  
**Symbol:** BTCUSDT  
**Budget:** 1 GB max  
**Start date:** 2026-09-13 (3 days ago, continuous recent data)

## Progress

The download script is running in the background.

**Strategy:**
- Downloads 1 hour at a time
- Saves immediately to disk (avoids memory errors)  
- Stops automatically at 1 GB limit
- Rate limited: 1.1 seconds between requests

**Estimated time:** 4-6 hours for 1 GB  
**Output location:** `data/raw/l2_orderbook/`

## What's Being Downloaded

Genuine L2 order book data:
- Incremental depth updates (price level changes)
- Multiple price levels per update
- Bid and ask sides
- Timestamps in milliseconds
- Can reconstruct full order book

## Files Created

- **Script:** `scripts/download_l2_data.py` (single clean downloader)
- **Output:** `data/raw/l2_orderbook/binance_futures_BTCUSDT_YYYY-MM-DD_HH.parquet`
- **Log:** `download_log.txt` (if you ran with logging)

## Next Steps (After Download Completes)

1. Validate data integrity
2. Test order book reconstruction
3. Integrate with LOB-X replay engine
4. Run backtests with real L2 depth

## To Check Progress

Run from PowerShell:
```powershell
Get-ChildItem data\raw\l2_orderbook\ | Measure-Object -Property Length -Sum
```

This shows total downloaded size.

## To Stop Early

Press Ctrl+C in the terminal running the script.
The data already downloaded will be preserved.
