# =====================================================================
#  LOB-X PERMANENT ENVIRONMENT SETUP — run THIS ONCE ever.
#  After this, every new PowerShell terminal "just works".
#
#  What it fixes forever:
#    1. cmake / ctest / cl.exe added to your Windows USER PATH
#       (no more Developer Shell required, no more "not recognized")
#    2. UTF-8 output enabled so emoji/arrows (→) never crash Python
#    3. A .pth file in your venv makes `import lobx` and
#       `import lobx_cpp` work from ANY folder (no PYTHONPATH juggling)
#    4. Creates dev.ps1 / test.ps1 convenience wrappers
# =====================================================================

$ErrorActionPreference = "Stop"

Write-Host "=== 1/4  Adding VS Build Tools + CMake to User PATH ===" -ForegroundColor Cyan

$pathsToAdd = @(
    "C:\Program Files (x86)\Microsoft Visual Studio\18\BuildTools\Common7\IDE\CommonExtensions\Microsoft\CMake\CMake\bin",
    "C:\Program Files (x86)\Microsoft Visual Studio\18\BuildTools\VC\Tools\MSVC\14.51.36231\bin\Hostx64\x64"
)

$userPath = [Environment]::GetEnvironmentVariable("Path", "User")
if (-not $userPath) { $userPath = "" }

foreach ($p in $pathsToAdd) {
    if ($userPath -notlike "*$p*") {
        [Environment]::SetEnvironmentVariable("Path", "$userPath;$p", "User")
        Write-Host "  + added to User PATH: $p"
    } else {
        Write-Host "  already present:    $p"
    }
}

Write-Host ""
Write-Host "=== 2/4  Enabling UTF-8 output permanently ===" -ForegroundColor Cyan
[Environment]::SetEnvironmentVariable("PYTHONIOENCODING", "utf-8", "User")
[Environment]::SetEnvironmentVariable("PYTHONUTF8", "1", "User")
Write-Host "  + PYTHONIOENCODING=utf-8  (fixes UnicodeEncodeError)"
Write-Host "  + PYTHONUTF8=1            (Python-wide UTF-8 mode)"

Write-Host ""
Write-Host "=== 3/4  Making lobx + lobx_cpp importable from anywhere ===" -ForegroundColor Cyan

$venvSite = Join-Path (Get-Location) ".venv\Lib\site-packages"
$backend  = Join-Path (Get-Location) "backend\python"
if (-not (Test-Path $venvSite)) { throw "venv site-packages not found: $venvSite" }

$pth = Join-Path $venvSite "lobx_path.pth"
Set-Content -Path $pth -Value $backend -Encoding UTF8
Write-Host "  + wrote $pth  ->  $backend"

Write-Host ""
Write-Host "=== 4/4  Creating dev.ps1 and test.ps1 wrappers ===" -ForegroundColor Cyan

$root = Get-Location

@"
# dev.ps1  — activate venv + build C++ engine + verify, all in ONE command.
# Usage:   .\dev.ps1
Set-Location "$root"
& .venv\Scripts\Activate.ps1
python scripts/setup_cpp_build.py
"@ | Set-Content -Path (Join-Path $root "dev.ps1") -Encoding UTF8

@"
# test.ps1 — run the FULL test suite (C++ + Python) and benchmarks.
# Usage:   .\test.ps1
Set-Location "$root"
& .venv\Scripts\Activate.ps1
Write-Host "=== C++ tests (21) ==="
ctest --test-dir backend\cpp\build -C Release --output-on-failure
Write-Host ""
Write-Host "=== Python tests (55) ==="
python -m pytest backend\python\tests -v --tb=short
Write-Host ""
Write-Host "=== Benchmarks ==="
python benchmarks\latency.py
python benchmarks\throughput.py
"@ | Set-Content -Path (Join-Path $root "test.ps1") -Encoding UTF8

Write-Host "  + created dev.ps1 / test.ps1"
Write-Host ""
Write-Host "================= DONE =================" -ForegroundColor Green
Write-Host "Close ALL PowerShell windows and open a NEW one."
Write-Host "Then from ANY folder you can run:"
Write-Host "  cmake --version        -> now works globally"
Write-Host "  import lobx_cpp        -> works from any folder"
Write-Host ""
Write-Host "From C:\Users\archa\lob-x you can also run:"
Write-Host "  .\dev.ps1    (rebuild C++ engine + verify)"
Write-Host "  .\test.ps1   (run ALL tests + benchmarks)"