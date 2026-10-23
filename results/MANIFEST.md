# LOB-X Results Manifest

This manifest records the commands used for the R-01–R-09 evidence artifacts.
Runtime JSON reports also record `data_source`, `parameter_hash`, `git_commit`,
and `git_dirty` when executed from a git checkout.

| Result | Command | Data source | Status |
|---|---|---|---|
| Calibration artifact | `python scripts/analyze_market.py --data-dir data/raw --pool --sample-interval 10000 --artifact-output reports/calibration.json` | real_l2 | Run on the execution machine; strict R-03 gate applies |
| Real-depth queue comparison | `python scripts/compare_real_depth.py --depth <depth.parquet> --trades <trades.parquet> --duration-ms 60000 --json-out results/real_depth_comparison.json` | real_l2 | Requires a selected real-L2 slice |
| Capture inventory | `python scripts/list_captures.py` | real_l2 | Lists available local captures |
| Multi-session comparison | `python scripts/run_research_suite.py --dates <date1,date2,...> --scenario conservative --output-dir results/research_suite` | real_l2 | Requires at least six sessions for inferential CI |
| Hawkes calibration | `python scripts/train_hawkes.py --parquet <trades.parquet> --output results/hawkes.json` | real_l2 trades | Thresholds are train-derived metadata |
| Hawkes on/off comparison | `python scripts/compare_hawkes.py --data-dir data/raw --dates <date1,date2> --hawkes-artifact results/hawkes.json` | real_l2 | Requires at least two sessions |

The current workspace has not been certified with the requested Windows
`pytest`, `ctest`, and three-minute engine-mirror run from this Linux session.
