# Build the C++ engine, run C++ and Python tests, then the engine benchmarks.
# Usage (from the repo root, inside the venv):  .\test.ps1
$ErrorActionPreference = "Stop"
python scripts/setup_cpp_build.py --tests
python -m pytest backend/python/tests -q
$env:PYTHONPATH = "backend/python"
python scripts/benchmark.py
