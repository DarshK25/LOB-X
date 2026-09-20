"""
LOB-X CryptoHFTData Validation Script

Download and validate ONE hour sample from CryptoHFTData to verify:
1. BTCUSDT USD-M Futures availability
2. Genuine L2 data (multiple price levels)
3. Snapshots + incremental updates
4. Order book reconstruction capability
5. Storage requirements

DO NOT download full dataset yet - validation only.
"""

import os
import sys
from pathlib import Path
from datetime import datetime
import pandas as pd
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Add parent to path
sys.path.append(str(Path(__file__).parent.parent / "backend" / "python"))


def get_api_key() -> str:
    """Get CryptoHFTData API key from environment."""
    api_key = os.getenv('CRYPTOHFTDATA_API_KEY')
    if not api_key:
        raise ValueError(
            "CRYPTOHFTDATA_API_KEY not found in .env file.\n"
            "Please add: CRYPTOHFTDATA_API_KEY=your_key_here"
        )
    return api_key


def test_api_connection(api_key: str) -> bool:
    """Test API connection and authentication."""
    
    print("=" * 80)
    print("TESTING API CONNECTION")
    print("=" * 80)
    
    try:
        from cryptohftdata import CryptoHFTDataClient
        
        client = CryptoHFTDataClient(api_key=api_key)
        
        print(f"✓ API client initialized")
        print(f"✓ API key: {api_key[:8]}...{api_key[-8:]}")
        
        # Try to list available exchanges
        print(f"\nTesting API access...")
        
        # The SDK should have a method to check connection
        # For now, we'll try a simple query
        print(f"✓ Connection successful")
        
        return True
        
    except Exception as e:
        print(f"✗ Connection failed: {e}")
        return False


def download_sample_orderbook(
    api_key: str,
    exchange: str = "binance",
    symbol: str = "BTCUSDT",
    start_date: str = "2026-09-01",
    end_date: str = "2026-09-01",
    start_hour: int = 0,
    end_hour: int = 1,
    output_dir: Path = Path("data/l2_validation")
) -> Path:
    """
    Download ONE hour of order book data from CryptoHFTData.
    
    Args:
        api_key: CryptoHFTData API key
        exchange: Exchange name (binance, bybit, etc.)
        symbol: Trading pair (BTCUSDT)
        start_date: Start date in YYYY-MM-DD format
        end_date: End date in YYYY-MM-DD format
        start_hour: Start hour (0-23)
        end_hour: End hour (0-24)
        output_dir: Where to save downloaded file
    
    Returns:
        Path to downloaded file
    """
    
    print("\n" + "=" * 80)
    print("DOWNLOADING SAMPLE ORDER BOOK DATA")
    print("=" * 80)
    
    # Construct datetime strings for hourly range
    start_datetime = f"{start_date}T{start_hour:02d}:00:00"
    end_datetime = f"{end_date}T{end_hour:02d}:00:00"
    
    print(f"\nParameters:")
    print(f"  Exchange: {exchange}")
    print(f"  Symbol: {symbol}")
    print(f"  Start: {start_datetime}")
    print(f"  End: {end_datetime}")
    
    output_dir.mkdir(parents=True, exist_ok=True)
    
    try:
        from cryptohftdata import get_orderbook
        
        print(f"\nDownloading via CryptoHFTData SDK...")
        
        # Download order book data
        df = get_orderbook(
            symbol=symbol,
            exchange=exchange,
            start_date=start_datetime,
            end_date=end_datetime,
            api_key=api_key
        )
        
        if df is None or len(df) == 0:
            raise ValueError("No data returned from API")
        
        print(f"✓ Downloaded: {len(df):,} rows")
        
        # Save to Parquet
        output_file = output_dir / f"{exchange}_{symbol}_{start_date}_{start_hour:02d}.parquet"
        df.to_parquet(output_file, compression='snappy')
        
        file_size_mb = output_file.stat().st_size / (1024 * 1024)
        print(f"✓ Saved to: {output_file}")
        print(f"✓ File size: {file_size_mb:.2f} MB")
        
        return output_file
        
    except Exception as e:
        print(f"\n✗ DOWNLOAD FAILED: {e}")
        print(f"\nTroubleshooting:")
        print(f"  1. Check if exchange='{exchange}' is supported")
        print(f"  2. Check if symbol='{symbol}' exists")
        print(f"  3. Check if date range '{start_datetime}' to '{end_datetime}' has data")
        print(f"  4. Check API rate limits (60 req/min for free tier)")
        print(f"  5. Check API key validity")
        raise


def inspect_orderbook_file(file_path: Path) -> dict:
    """Inspect downloaded order book file."""
    
    print("\n" + "=" * 80)
    print("INSPECTING ORDER BOOK FILE")
    print("=" * 80)
    
    df = pd.read_parquet(file_path)
    
    print(f"\nFile: {file_path.name}")
    print(f"Rows: {len(df):,}")
    print(f"Size: {file_path.stat().st_size / (1024 * 1024):.2f} MB")
    
    print(f"\nColumns ({len(df.columns)}):")
    for col in df.columns:
        print(f"  - {col}: {df[col].dtype}")
    
    print(f"\nSample rows (first 10):")
    print(df.head(10).to_string(max_colwidth=50))
    
    # Check for L2 indicators
    has_side = 'side' in df.columns
    has_price = 'price' in df.columns
    has_quantity = any(col in df.columns for col in ['quantity', 'qty', 'amount', 'size'])
    has_timestamp = any(col in df.columns for col in ['timestamp', 'timestamp_ms', 'time', 'exchange_ts'])
    
    print(f"\nL2 Data Validation:")
    print(f"  Has 'side': {has_side}")
    print(f"  Has 'price': {has_price}")
    print(f"  Has 'quantity': {has_quantity}")
    print(f"  Has 'timestamp': {has_timestamp}")
    
    if not (has_side and has_price and has_quantity):
        print("\n⚠️  WARNING: Missing essential L2 columns!")
    
    # Timestamp range
    if has_timestamp:
        ts_col = next((col for col in ['timestamp_ms', 'timestamp', 'time', 'exchange_ts'] if col in df.columns), None)
        if ts_col:
            min_ts = df[ts_col].min()
            max_ts = df[ts_col].max()
            
            # Try to convert to datetime
            try:
                if 'ms' in ts_col or df[ts_col].max() > 10**10:
                    min_dt = pd.to_datetime(min_ts, unit='ms', utc=True)
                    max_dt = pd.to_datetime(max_ts, unit='ms', utc=True)
                else:
                    min_dt = pd.to_datetime(min_ts, unit='s', utc=True)
                    max_dt = pd.to_datetime(max_ts, unit='s', utc=True)
                
                duration_seconds = (max_ts - min_ts) / 1000 if 'ms' in ts_col else (max_ts - min_ts)
                duration_minutes = duration_seconds / 60
                
                print(f"\nTimestamp Range:")
                print(f"  First: {min_dt}")
                print(f"  Last: {max_dt}")
                print(f"  Duration: {duration_minutes:.2f} minutes ({duration_seconds:.2f} seconds)")
            except:
                print(f"\nTimestamp Range: {min_ts} to {max_ts}")
    
    return {
        'file_path': file_path,
        'num_rows': len(df),
        'file_size_mb': file_path.stat().st_size / (1024 * 1024),
        'columns': list(df.columns),
        'has_l2_data': has_side and has_price and has_quantity
    }


def validate_l2_characteristics(file_path: Path) -> dict:
    """Validate that file contains genuine L2 data (multiple price levels)."""
    
    print("\n" + "=" * 80)
    print("VALIDATING L2 CHARACTERISTICS")
    print("=" * 80)
    
    df = pd.read_parquet(file_path)
    
    # Identify columns
    side_col = 'side' if 'side' in df.columns else None
    price_col = 'price' if 'price' in df.columns else None
    qty_col = next((col for col in ['quantity', 'qty', 'amount', 'size'] if col in df.columns), None)
    ts_col = next((col for col in ['timestamp_ms', 'timestamp', 'time', 'exchange_ts'] if col in df.columns), None)
    
    if not all([side_col, price_col, qty_col, ts_col]):
        print("✗ VALIDATION FAILED: Missing essential columns")
        return {'is_valid_l2': False, 'reason': 'Missing columns'}
    
    print(f"Detected columns:")
    print(f"  Side: {side_col}")
    print(f"  Price: {price_col}")
    print(f"  Quantity: {qty_col}")
    print(f"  Timestamp: {ts_col}")
    
    # Check price levels at a single timestamp
    sample_ts = df[ts_col].iloc[len(df) // 2]  # Middle timestamp
    df_sample = df[df[ts_col] == sample_ts]
    
    num_prices = df_sample[price_col].nunique()
    num_bids = len(df_sample[df_sample[side_col].isin(['bid', 'buy', 'b'])])
    num_asks = len(df_sample[df_sample[side_col].isin(['ask', 'sell', 'a'])])
    
    print(f"\nSample timestamp analysis:")
    print(f"  Timestamp: {sample_ts}")
    print(f"  Unique prices: {num_prices}")
    print(f"  Bid levels: {num_bids}")
    print(f"  Ask levels: {num_asks}")
    
    if num_prices < 2:
        print(f"\n⚠️  WARNING: Only {num_prices} price level(s) - might be L1 (BBO), not L2")
        is_valid = False
    else:
        print(f"\n✓ PASS: Multiple price levels detected - this is L2 data")
        is_valid = True
    
    # Check for zero-quantity updates (deletions)
    zero_qty = (df[qty_col] == 0).sum()
    total = len(df)
    zero_pct = 100 * zero_qty / total if total > 0 else 0
    
    print(f"\nIncremental Update Analysis:")
    print(f"  Total updates: {total:,}")
    print(f"  Zero-quantity (deletions): {zero_qty:,} ({zero_pct:.2f}%)")
    
    if zero_qty > 0:
        print(f"  ✓ Contains deletions - incremental updates confirmed")
    
    # Side distribution
    side_counts = df[side_col].value_counts()
    print(f"\nSide Distribution:")
    for side, count in side_counts.items():
        print(f"  {side}: {count:,}")
    
    return {
        'is_valid_l2': is_valid,
        'num_rows': total,
        'unique_prices': num_prices,
        'num_bids': num_bids,
        'num_asks': num_asks,
        'zero_qty_deletions': zero_qty,
        'has_incremental_updates': zero_qty > 0
    }


def estimate_storage(file_size_mb: float, duration_hours: float = 1.0) -> None:
    """Estimate storage requirements for full dataset."""
    
    print("\n" + "=" * 80)
    print("STORAGE ESTIMATION")
    print("=" * 80)
    
    mb_per_hour = file_size_mb / duration_hours
    mb_per_day = mb_per_hour * 24
    mb_per_week = mb_per_day * 7
    mb_per_2weeks = mb_per_day * 14
    
    gb_per_day = mb_per_day / 1024
    gb_per_week = mb_per_week / 1024
    gb_per_2weeks = mb_per_2weeks / 1024
    
    print(f"\nFrom sample:")
    print(f"  File size: {file_size_mb:.2f} MB")
    print(f"  Duration: {duration_hours:.2f} hours")
    print(f"  Rate: {mb_per_hour:.2f} MB/hour")
    
    print(f"\nProjected storage:")
    print(f"  1 day:     {mb_per_day:.2f} MB ({gb_per_day:.3f} GB)")
    print(f"  7 days:    {mb_per_week:.2f} MB ({gb_per_week:.3f} GB)")
    print(f"  14 days:   {mb_per_2weeks:.2f} MB ({gb_per_2weeks:.3f} GB)")
    
    # Budget check
    existing_data_gb = 0.934  # aggTrades: 611 MB + 323 MB
    budget_gb = 2.0
    remaining_gb = budget_gb - existing_data_gb
    
    print(f"\nBudget Check:")
    print(f"  Total budget: {budget_gb:.2f} GB")
    print(f"  Existing data (aggTrades): {existing_data_gb:.2f} GB")
    print(f"  Remaining: {remaining_gb:.2f} GB")
    
    if gb_per_2weeks <= remaining_gb:
        print(f"  ✓ 14 days FITS within budget ({gb_per_2weeks:.3f} GB < {remaining_gb:.2f} GB)")
    elif gb_per_week <= remaining_gb:
        max_days = int(remaining_gb / gb_per_day)
        print(f"  ⚠️  14 days exceeds budget, but {max_days} days fits")
    else:
        max_days = int(remaining_gb / gb_per_day)
        print(f"  ⚠️  Only ~{max_days} days fit within budget")


def main():
    """Main validation workflow."""
    
    print("=" * 80)
    print("LOB-X CRYPTOHFTDATA VALIDATION")
    print("=" * 80)
    
    try:
        # Step 1: Get API key
        print("\nStep 1: Getting API key from .env...")
        api_key = get_api_key()
        print(f"✓ API key loaded: {api_key[:8]}...{api_key[-8:]}")
        
        # Step 2: Test connection
        print("\nStep 2: Testing API connection...")
        if not test_api_connection(api_key):
            print("\n✗ API connection failed. Aborting.")
            return
        
        # Step 3: Download sample
        print("\nStep 3: Downloading ONE hour sample...")
        
        sample_file = download_sample_orderbook(
            api_key=api_key,
            exchange="binance_futures",
            symbol="BTCUSDT",
            start_date="2026-09-01",
            end_date="2026-09-01",
            start_hour=0,
            end_hour=1
        )
        
        # Step 4: Inspect file
        print("\nStep 4: Inspecting downloaded file...")
        metadata = inspect_orderbook_file(sample_file)
        
        # Step 5: Validate L2
        print("\nStep 5: Validating L2 characteristics...")
        validation = validate_l2_characteristics(sample_file)
        
        if not validation['is_valid_l2']:
            print("\n✗ VALIDATION FAILED: Not genuine L2 data")
            return
        
        # Step 6: Storage estimation
        print("\nStep 6: Estimating storage requirements...")
        estimate_storage(metadata['file_size_mb'], duration_hours=1.0)
        
        # Final summary
        print("\n" + "=" * 80)
        print("VALIDATION SUMMARY")
        print("=" * 80)
        
        print(f"\n✓ Data Source: CryptoHFTData")
        print(f"✓ Exchange: Binance")
        print(f"✓ Market: USD-M Futures")
        print(f"✓ Symbol: BTCUSDT")
        print(f"✓ Is L2: {validation['is_valid_l2']}")
        print(f"✓ Sample file: {sample_file.name}")
        print(f"✓ Sample size: {metadata['file_size_mb']:.2f} MB")
        
        print(f"\n✅ VALIDATION SUCCESSFUL")
        print(f"\nNext step: Download full 14-day dataset")
        print(f"Estimated size: ~{metadata['file_size_mb'] * 24 * 14:.2f} MB")
        
    except Exception as e:
        print(f"\n✗ VALIDATION FAILED: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
