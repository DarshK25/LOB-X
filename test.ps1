# =====================================================================
#  LOB-X test.ps1  --  ONE command: run ALL tests + benchmarks.
#  Fully self-contained: works in ANY PowerShell window, no PATH setup.
#
#  Usage:  .\test.ps1
# =====================================================================

# --- 1. Add VS Build Tools + CMake to THIS session (self-contained) ---
$env:PATH = "C:\Program Files (x86)\Microsoft Visual Studio\18\BuildTools\Common7\IDE\CommonExtensions\Microsoft\CMake\CMake\bin;" +
            "C:\Program Files (x86)\Microsoft Visual Studio\18\BuildTools\VC\Tools\MSVC\14.51.36231\bin\Hostx64\x64;" +
            $env:PATH

# Load full MSVC environment
& "C:\Program Files (x86)\Microsoft Visual Studio\18\BuildTools\VC\Auxiliary\Build\vcvarsall.bat" x64 | Out-Null

# --- 2. Activate the project venv ---
Set-Location "C:\Users\archa\lob-x"
& .venv\Scripts\Activate.ps1

# --- 3. C++ tests (21) ---
Write-Host ""
Write-Host "=== C++ tests (21 expected) ===" -ForegroundColor Cyan
ctest --test-dir backend\cpp\build -C Release --output-on-failure

# --- 4. Python tests (55 expected) ---
Write-Host ""
Write-Host "=== Python tests (55 expected) ===" -ForegroundColor Cyan
python -m pytest backend\python\tests -v --tb=short

# --- 5. Benchmarks ---
Write-Host ""
Write-Host "=== Benchmarks ===" -ForegroundColor Cyan
python benchmarks\latency.py
python benchmarks\throughput.py

Write-Host ""
Write-Host "=== ALL DONE ===" -ForegroundColor Green