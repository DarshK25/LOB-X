# Set up MSVC + CMake environment, then build the C++ engine
$env:PATH = "C:\Program Files (x86)\Microsoft Visual Studio\18\BuildTools\Common7\IDE\CommonExtensions\Microsoft\CMake\CMake\bin;C:\Program Files (x86)\Microsoft Visual Studio\18\BuildTools\VC\Tools\MSVC\14.51.36231\bin\Hostx64\x64;" + $env:PATH

# Source vcvarsall for full MSVC library paths
& "C:\Program Files (x86)\Microsoft Visual Studio\18\BuildTools\VC\Auxiliary\Build\vcvarsall.bat" x64 | Out-Null

Set-Location C:\Users\archa\lob-x
& .venv/Scripts/Activate.ps1

Write-Host "=== Verifying tools ==="
Write-Host "cl.exe: $(Get-Command cl -ErrorAction SilentlyContinue | Select-Object -Expand Path)"
Write-Host "cmake: $(Get-Command cmake -ErrorAction SilentlyContinue | Select-Object -Expand Path)"

Write-Host ""
Write-Host "=== Building C++ Engine ==="
python scripts/setup_cpp_build.py