# =====================================================================
#  LOB-X dev.ps1  --  ONE command: activate venv + build C++ engine.
#  Fully self-contained: works in ANY PowerShell window, no PATH setup.
#
#  Usage:  .\dev.ps1
# =====================================================================

# --- 1. Add VS Build Tools + CMake to THIS session (self-contained) ---
$env:PATH = "C:\Program Files (x86)\Microsoft Visual Studio\18\BuildTools\Common7\IDE\CommonExtensions\Microsoft\CMake\CMake\bin;" +
            "C:\Program Files (x86)\Microsoft Visual Studio\18\BuildTools\VC\Tools\MSVC\14.51.36231\bin\Hostx64\x64;" +
            $env:PATH

# Load full MSVC environment (INCLUDE/LIB for the compiler)
& "C:\Program Files (x86)\Microsoft Visual Studio\18\BuildTools\VC\Auxiliary\Build\vcvarsall.bat" x64 | Out-Null

# --- 2. Activate the project venv ---
Set-Location "C:\Users\archa\lob-x"
& .venv\Scripts\Activate.ps1

# --- 3. Rebuild the C++ engine (idempotent - only recompiles changes) ---
Write-Host ""
Write-Host "=== Building C++ Engine (lobx_cpp) ===" -ForegroundColor Cyan
python scripts/setup_cpp_build.py

Write-Host ""
Write-Host "=== DONE. Environment ready. ===" -ForegroundColor Green