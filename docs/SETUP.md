# LOB-X — Developer Setup Guide

Follow this top to bottom on first setup. After that, re-run
`scripts/verify_environment.py` any time to spot-check your machine.

---

## 1. Prerequisites

| Tool | Version | Why |
|------|---------|-----|
| Python | 3.11+ (built/tested on 3.13) | strategy, pipeline, tests |
| Git | any recent | version control |
| CMake | 3.20+ | configures the C++ build |
| C++ compiler | MSVC (Windows) / gcc or clang (Linux/Mac) | builds the matching engine |

### Windows
1. Install **Visual Studio Build Tools**: https://visualstudio.microsoft.com/visual-cpp-build-tools/
   - During install, check **"Desktop development with C++"**
2. Install **CMake**: https://cmake.org/download/
   - During install, check **"Add CMake to system PATH for all users"**
3. **Restart your terminal** after both installs so PATH is updated.

### macOS
```bash
xcode-select --install
brew install cmake
```

### Linux (Debian/Ubuntu)
```bash
sudo apt update && sudo apt install build-essential cmake
```

---

## 2. Clone and Set Up Python

```bash
git clone <repo-url> LOB-X
cd LOB-X
python -m venv .venv
```

Activate the virtual environment:
- **Windows PowerShell**: `.venv\Scripts\Activate.ps1`
- **macOS/Linux**: `source .venv/bin/activate`

```bash
pip install -r requirements.txt
pip install -r requirements-dev.txt
```

---

## 3. Build the C++ Engine

```bash
python scripts/setup_cpp_build.py
```

Or manually (useful for debugging build issues):
```bash
cmake -S backend/cpp -B backend/cpp/build -A x64
cmake --build backend/cpp/build --config Release --target lobx_cpp
```

Verify the build:
```bash
python -c "import sys; sys.path.insert(0,'backend/python'); import lobx_cpp; print('lobx_cpp OK')"
```

Run the C++ unit tests (21/21 expected to pass):
```bash
cmake --build backend/cpp/build --config Release --target lobx_tests
ctest --test-dir backend/cpp/build -C Release --output-on-failure
```

---

## 4. Verify Your Full Environment

```bash
python scripts/verify_environment.py
```

Fix anything flagged before continuing.

---

## 5. Run the System

| Terminal | Command | Purpose |
|----------|---------|---------|
| 1 | `python scripts/run_collector.py` | Live collector (depth + trades → Parquet) |
| 2 | `python scripts/watch_live.py` | Live bid/ask ticker |
| 3 | `python scripts/verify_engine_mirror.py` | C++ vs Python book sync check |
| 4 | `python scripts/run_live_strategy.py --strategy fixed` | Phase 2: baseline strategy |
| 4 | `python scripts/run_live_strategy.py --strategy as` | Phase 2: AS strategy |
| Any | `python scripts/read_data.py` | Inspect collected Parquet files |

---

## 6. Run the Test Suite

```bash
pytest backend/python/tests -v
```

Expected output (Phase 2 complete): **all tests pass**, no warnings.

---

## 7. Git Workflow

- Branch naming: `feature/<short-description>`
- No direct pushes to `main` — PR + at least one review
- Log non-obvious design decisions in `docs/DECISIONS.md`

---

## 8. Project Structure

```
LOB-X/
├── backend/
│   ├── cpp/                    # matching engine (C++17, CMake, Catch2)
│   └── python/lobx/
│       ├── market_data/        # WS client, order-book sync, engine mirror
│       ├── storage/            # rotating Parquet writer
│       ├── strategy/           # Strategy interface + implementations
│       ├── execution/          # shadow fills, inventory tracking
│       ├── analytics/          # volatility, spread, PnL, OFI
│       ├── live/               # wires Collector → Strategy → ShadowEngine
│       ├── models/             # AS / Almgren-Chriss / Hawkes math
│       └── risk/               # position limits, drawdown monitoring
├── scripts/                    # all runnable entrypoints
├── data/raw/                   # Parquet output (gitignored)
├── docs/                       # architecture notes, setup, decisions log
└── backend/python/tests/       # pytest test suite
```

---

## 9. Troubleshooting

| Symptom | Likely Cause | Fix |
|---------|-------------|-----|
| `cmake: command not found` | PATH not refreshed after install | Restart terminal |
| `cl.exe` not found | VS Build Tools missing C++ workload | Re-run VS installer, add "Desktop development with C++" |
| `ImportError: DLL load failed` on `lobx_cpp` | Built for different Python version | Rebuild: `python scripts/setup_cpp_build.py` |
| `UnicodeEncodeError` in console | Windows console not UTF-8 | Already handled — all scripts call `sys.stdout.reconfigure(encoding='utf-8')` |
| `verify_engine_mirror.py` shows MISMATCH | Tick conversion or synthetic ID issue | Re-check `TICKS_PER_UNIT` in `engine_mirror.py` |
| `ModuleNotFoundError: lobx` | venv not activated or path wrong | Run from repo root; ensure `backend/python` is in `sys.path` |

---

## 10. One-Line Quickstart (New Teammate)

```powershell
git clone <repo-url> LOB-X
cd LOB-X
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt -r requirements-dev.txt
python scripts/verify_environment.py
```
