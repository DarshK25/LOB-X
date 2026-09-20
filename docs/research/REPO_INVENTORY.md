# LOB-X Repository Inventory

| Category | Files | Status |
|----------|-------|--------|
| **C++ Engine** | backend/cpp/* | CANONICAL |
| **Python Core** | lobx/{execution,backtest,models,strategy,analytics} | CANONICAL |
| **Risk & Portfolio** | lobx/risk, execution/{inventory,portfolio,shadow_engine} | CANONICAL |
| **Market Data** | lobx/market_data/* | CANONICAL |
| **API & Storage** | lobx/{api,storage} | CANONICAL |
| **Live Trading** | lobx/live/quote_loop.py | CANONICAL |
| **Simulation** | lobx/simulation/* | RESEARCH |
| **Entry Scripts** | scripts/{run_backtest,run_live_strategy,run_collector,lobx_cli}.py | CANONICAL |
| **Data Scripts** | scripts/{download_*,validate_*,morning_check}.py | CANONICAL |
| **Analysis Scripts** | scripts/{analyze_market,analyze_backtest_regimes,sweep_parameters}.py | RESEARCH |
| **Audit Scripts** | scripts/audit_*.py (3 files) | ONE-OFF |
| **Duplicate Backtests** | scripts/{backtest_simple,backtest_live_visual,run_binance_experiment}.py | DUPLICATE |
| **Utility Scripts** | scripts/{read_data,generate_*,calibration_diagnostics}.py | UTILITY |
| **Test Scripts** | scripts/{test_reconnect,verify_*,inspect_*}.py | ONE-OFF |
| **Setup Scripts** | scripts/setup_*.{py,ps1} | CANONICAL |
| **Benchmarks** | benchmarks/* | CANONICAL |
| **Tests** | backend/python/tests/*.py (19 files) | CANONICAL |
| **Phase Tests** | test_phase_*.py (2 files) | RENAME |
| **Docs Math** | docs/math/*.md (5 files) | CANONICAL |
| **Docs Research** | docs/research/*.md | CANONICAL |
| **Docs General** | docs/*.md (8 files) | CANONICAL |
| **Reports** | reports/backtests/*.json | GENERATED |
| **Results** | results/* | GENERATED |
| **Data** | data/raw/*.parquet | PRESERVED |
| **Data Manifests** | data/*.md | CANONICAL |
| **HFTENGINE** | _reference/hftengine/* | REFERENCE |
| **PowerShell Helpers** | _*.ps1 (5 files) | UTILITY |
| **Configs** | configs/* | CANONICAL |
