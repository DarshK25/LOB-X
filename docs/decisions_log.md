# Decisions Log

## 2026-08-05: Phase 1 C++ Build Configuration
**Issue**: `pybind11` defaulted to the highest Python version available on the system path (Python 3.14), causing a `DLL load failed` when attempting to import the compiled `lobx_cpp` extension into the Python 3.13 environment.
**Decision**: Explicitly pass `-DPython_EXECUTABLE="C:/Program Files/Python313/python.exe"` in `scripts/setup_cpp_build.py` to force CMake to build the extension for the active Python 3.13 environment.

## 2026-08-05: Phase 3 Analytics Interface
**Issue**: C++ `OrderBook` `best_bid()` and `best_ask()` methods were changed in a previous phase from returning a tuple `(price, qty)` to returning a scalar `price` float, adding new `best_bid_qty()`/`best_ask_qty()` methods. This caused the legacy Phase 3 analytics unit tests (`test_microprice.py` etc.) to crash with "cannot unpack non-iterable float object".
**Decision**: The Python analytics modules (`microprice.py`, `imbalance.py`, etc.) and the Catch2 test suite were comprehensively updated to correctly consume the new volume-aware API (`book.best_bid()`, `book.best_bid_qty()`).

## 2026-08-06: Phase 3 Kappa/Gamma Calibration Validity
**Issue**: The previous Kappa exponential fit yielded statistically meaningless results (R² < 0.01) on sparse data, yet derived a pathological Gamma value (~4.94) which was quietly accepted.
**Decision**: 
1. The Kappa fit was gated behind a strict R² ≥ 0.50 floor; fits failing this will properly abort rather than providing nonsense. 
2. A comparison with Falces Marín et al. (2022) was added, explicitly noting their genetically optimized BTC-USD Gamma of 0.624 and range of [0.01, 0.9], confirming that un-gated values like 4.94 are unviable.

## 2026-08-15: Timezone Bug in morning_check.py and Kappa Calibration Final Finding

**Issue 1 — Timezone:** `datetime.fromtimestamp()` on Parquet `event_time` data was unqualified, defaulting to the machine's local timezone (IST, UTC+5:30). This made the reported depth-event coverage window disagree with UTC log timestamps by ~5.5 hours.
**Decision**: Pass `tz=timezone.utc` to all `datetime.fromtimestamp()` calls on `event_time` data in `morning_check.py`. Post-fix, depth coverage start matches log start to within 2 seconds. The coverage end at `2026-08-14 23:59:59+00:00` is correct — the Parquet file rotated at UTC midnight by design.

**Issue 2 — Kappa calibration final finding:** Two resolution strategies were attempted on real overnight data (9,558,861 depth rows, 1,378,952 trades from a genuine 14.5-hour run):
1. Whole-tick resolution (0.01 USD / 10 buckets): R² = 0.355 — fill rates non-monotone between ticks 2–10.
2. Sub-tick resolution (0.001 USD / 100 buckets): R² = 0.119 — the finer grid exposed that BTCUSDT fills only land at 0.005 USD multiples (half-tick intervals from mid), because exchange prices are discrete at 0.01 USD. Sub-tick distances are mathematically impossible; the 9 empty bins between each real point further degrade the OLS fit.
**Decision**: Accept the negative result as a citable finding. The standard Avellaneda-Stoikov exponential fill-intensity model does not calibrate on BTCUSDT's tight one-tick-spread regime with this method. The R² ≥ 0.50 gate correctly blocks both fits. Future work should either fit only the observed half-tick bins (0.005, 0.015, 0.025 … USD) as the distance axis, or switch to a Hawkes-process fill model better suited to discrete-tick, tight-spread assets.
