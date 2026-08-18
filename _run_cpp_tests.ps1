# Build and run C++ tests
$env:PATH = "C:\Program Files (x86)\Microsoft Visual Studio\18\BuildTools\Common7\IDE\CommonExtensions\Microsoft\CMake\CMake\bin;C:\Program Files (x86)\Microsoft Visual Studio\18\BuildTools\VC\Tools\MSVC\14.51.36231\bin\Hostx64\x64;" + $env:PATH

& "C:\Program Files (x86)\Microsoft Visual Studio\18\BuildTools\VC\Auxiliary\Build\vcvarsall.bat" x64 | Out-Null

Set-Location C:\Users\archa\lob-x
& .venv/Scripts/Activate.ps1

Write-Host "=== Building C++ tests ==="
cmake --build backend/cpp/build --config Release --target lobx_tests

Write-Host ""
Write-Host "=== Running C++ tests ==="
ctest --test-dir backend/cpp/build -C Release --output-on-failure