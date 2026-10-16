"""
LOB-X L2 Data Downloader - CryptoHFTData

Download continuous L2 order book data up to 1 GB budget.
Downloads in small 1-hour chunks to avoid memory errors.
"""

import os
import sys
from pathlib import Path
from datetime import datetime, timedelta
import pandas as pd
from dotenv import load_dotenv
import time

load_dotenv()
API_KEY = os.getenv("CRYPTOHFTDATA_API_KEY")

from cryptohftdata import get_orderbook


def download_hour_chunk(symbol, exchange, date, hour, output_dir):
    """Download 1 hour of data and save immediately to avoid memory issues."""
    
    start_time = f"{date} {hour:02d}:00:00"
    end_time = f"{date} {hour:02d}:59:59"
    
    print(f"  [{hour:02d}:00] Downloading...", end=" ", flush=True)
    
    try:
        df = get_orderbook(
            symbol=symbol,
            exchange=exchange,
            start_date=start_time,
            end_date=end_time,
            api_key=API_KEY
        )
        
        if df is None or len(df) == 0:
            print("EMPTY")
            return None, 0
        
        # Save immediately
        output_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{exchange}_{symbol}_{date}_{hour:02d}.parquet"
        filepath = output_dir / filename
        
        df.to_parquet(filepath, compression='snappy', index=False)
        
        size_mb = filepath.stat().st_size / (1024 * 1024)
        print(f"OK ({len(df):,} rows, {size_mb:.1f} MB)")
        
        return filepath, size_mb
        
    except Exception as e:
        print(f"ERROR: {e}")
        return None, 0


def main():
    print("=" * 80)
    print("LOB-X L2 DATA DOWNLOADER")
    print("=" * 80)
    
    BUDGET_GB = 1.0
    EXCHANGE = "binance_futures"
    SYMBOL = "BTCUSDT"
    OUTPUT_DIR = Path("data/raw/l2_orderbook")
    
    print(f"\nConfig: {SYMBOL} @ {EXCHANGE}, Budget: {BUDGET_GB} GB")
    print(f"Output: {OUTPUT_DIR}")
    
    # Start from 3 days ago (recent continuous data)
    start_date = datetime.now() - timedelta(days=3)
    
    print(f"\nStarting from: {start_date.strftime('%Y-%m-%d')}")
    print("\nDownloading hour by hour until 1 GB limit reached...")
    print("Press Ctrl+C to stop early.\n")
    
    downloaded_files = []
    total_mb = 0
    budget_mb = BUDGET_GB * 1024
    
    current_date = start_date
    
    try:
        while total_mb < budget_mb:
            date_str = current_date.strftime('%Y-%m-%d')
            
            print(f"\n{date_str}:")
            
            for hour in range(24):
                if total_mb >= budget_mb:
                    print(f"\n✅ Budget limit reached ({total_mb:.1f} MB)")
                    break
                
                filepath, size_mb = download_hour_chunk(
                    SYMBOL, EXCHANGE, date_str, hour, OUTPUT_DIR
                )
                
                if filepath:
                    downloaded_files.append(filepath)
                    total_mb += size_mb
                
                # Small delay to respect rate limits
                time.sleep(1.1)
            
            if total_mb >= budget_mb:
                break
            
            current_date += timedelta(days=1)
    
    except KeyboardInterrupt:
        print("\n\nStopped by user.")
    
    # Summary
    print(f"\n{'=' * 80}")
    print("DOWNLOAD COMPLETE")
    print(f"{'=' * 80}")
    print(f"Files: {len(downloaded_files)}")
    print(f"Total: {total_mb:.2f} MB ({total_mb/1024:.2f} GB)")
    print(f"Location: {OUTPUT_DIR}")
    
    if downloaded_files:
        first_file = downloaded_files[0].name
        last_file = downloaded_files[-1].name
        print(f"\nRange: {first_file} → {last_file}")


if __name__ == "__main__":
    if not API_KEY:
        print("ERROR: CRYPTOHFTDATA_API_KEY not found in .env")
        sys.exit(1)
    
    main()
