"""
setup_cpp_build.py — automates the full C++ engine build on Windows.

Run this ONCE to set up the C++ matching engine (lobx_cpp.pyd):
  python scripts/setup_cpp_build.py

What it does (in order)
────────────────────────
1. Checks that Visual Studio Build Tools are installed (cl.exe in PATH).
2. Installs pybind11 via pip if missing.
3. Runs cmake to configure the build.
4. Runs cmake --build to compile lobx_cpp.pyd.
5. Copies lobx_cpp*.pyd next to backend/python/lobx/ so Python can find it.
6. Runs a quick import test to confirm it works.

Prerequisites (must install manually first — this script cannot do it):
  Visual Studio 2022 Build Tools with "Desktop development with C++" workload.
  Download from: https://visualstudio.microsoft.com/visual-cpp-build-tools/
  → Check "Desktop development with C++" in the installer.
  → Restart PowerShell AFTER installation so cl.exe is in PATH.

If you get "cmake not found", install CMake from: https://cmake.org/download/
  → During installation, select "Add CMake to system PATH for all users".
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CPP_DIR = ROOT / "backend" / "cpp"
BUILD_DIR = CPP_DIR / "build"
PYTHON_LOBX = ROOT / "backend" / "python" / "lobx"


def run(cmd: list[str], cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess:
    print(f"\n  $ {' '.join(str(c) for c in cmd)}")
    result = subprocess.run(cmd, cwd=cwd, capture_output=False, text=True)
    if check and result.returncode != 0:
        print(f"\n  FAILED with exit code {result.returncode}")
        print("  See the output above for details.")
        sys.exit(1)
    return result


def check_cl() -> bool:
    """Check that cl.exe (MSVC compiler) is in PATH, or that Visual Studio is installed."""
    found = shutil.which("cl")
    if found:
        print(f"  cl.exe found: {found}")
        return True
    
    # On Windows, try to find Visual Studio via vswhere.exe
    import os
    vswhere_path = Path("C:/Program Files (x86)/Microsoft Visual Studio/Installer/vswhere.exe")
    if vswhere_path.exists():
        try:
            res = subprocess.run(
                [str(vswhere_path), "-latest", "-products", "*", "-property", "installationPath"],
                capture_output=True, text=True, check=True
            )
            path = res.stdout.strip()
            if path:
                print(f"  Visual Studio installation detected at: {path}")
                print("  (Note: CMake will locate MSVC automatically even though 'cl' is not in current PATH.)")
                return True
        except Exception:
            pass

    print("\n  ERROR: cl.exe not found in PATH.")
    print("  You need Visual Studio Build Tools with C++ workload.")
    print("  Download: https://visualstudio.microsoft.com/visual-cpp-build-tools/")
    print("  After installing, open 'Developer PowerShell for VS 2022' or make sure it is installed.")
    return False


def check_cmake() -> bool:
    found = shutil.which("cmake")
    if found:
        print(f"  cmake found: {found}")
        return True
    print("\n  ERROR: cmake not found in PATH.")
    print("  Download from: https://cmake.org/download/")
    print("  Select 'Add CMake to system PATH' during installation.")
    return False


def install_pybind11() -> None:
    try:
        import pybind11  # noqa: F401
        print(f"  pybind11 already installed.")
    except ImportError:
        print("  Installing pybind11…")
        run([sys.executable, "-m", "pip", "install", "pybind11"])


def get_pybind11_cmake_dir() -> str:
    import pybind11
    cmake_dir = pybind11.get_cmake_dir()
    print(f"  pybind11 cmake dir: {cmake_dir}")
    return cmake_dir


def build() -> None:
    pybind11_dir = get_pybind11_cmake_dir()

    print("\n  Configuring (cmake)…")
    BUILD_DIR.mkdir(exist_ok=True)
    run([
        "cmake",
        "-S", str(CPP_DIR),
        "-B", str(BUILD_DIR),
        f"-Dpybind11_DIR={pybind11_dir}",
        f"-DPython_EXECUTABLE={sys.executable}",
        "-DCMAKE_BUILD_TYPE=Release",
        "-A", "x64",   # 64-bit target — must match your Python bitness
    ])

    print("\n  Compiling (cmake --build --target lobx_cpp)…")
    run([
        "cmake", "--build", str(BUILD_DIR),
        "--target", "lobx_cpp",
        "--config", "Release",
        "--parallel",
    ])


def find_and_copy_pyd() -> Path:
    """Find the compiled lobx_cpp*.pyd and copy it next to lobx/__init__.py."""
    pyds = list(BUILD_DIR.rglob("lobx_cpp*.pyd"))
    if not pyds:
        print("\n  ERROR: lobx_cpp*.pyd not found in build directory.")
        print("  Check the CMakeLists.txt — pybind11 may not have been found.")
        sys.exit(1)

    pyd = pyds[0]
    dest = PYTHON_LOBX.parent / pyd.name   # backend/python/lobx_cpp*.pyd
    shutil.copy2(pyd, dest)
    print(f"\n  Copied {pyd.name} to {dest}")
    return dest


def verify_import() -> None:
    """Quick import test — confirms the .pyd works with this Python."""
    print("\n  Running import test…")
    result = subprocess.run(
        [sys.executable, "-c",
         "import sys; sys.path.insert(0, 'backend/python'); "
         "import lobx_cpp; "
         "book = lobx_cpp.OrderBook(); "
         "o = lobx_cpp.Order(1, 100, 10, True); "
         "trades = book.add_limit_order(o); "
         "print('lobx_cpp import OK. OrderBook.add_limit_order() returned', len(trades), 'trades.')"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        print("  " + result.stdout.strip())
        print("\n  SUCCESS — lobx_cpp is working.")
        print("  You can now run: python scripts/run_collector.py  (without --no-engine-mirror)")
    else:
        print(f"  FAILED: {result.stderr.strip()}")
        print("  The .pyd was copied but import failed — likely a Python version mismatch.")
        print("  Make sure you're building with the same Python you run scripts with.")


def main() -> None:
    print("=" * 64)
    print("  LOB-X C++ Engine Build Setup")
    print("=" * 64)

    if not check_cl():
        sys.exit(1)
    if not check_cmake():
        sys.exit(1)

    install_pybind11()
    build()
    find_and_copy_pyd()
    verify_import()

    print("\n" + "=" * 64)
    print("  BUILD COMPLETE")
    print("  Next steps:")
    print("    python scripts/run_collector.py          (full pipeline with C++ engine)")
    print("    python scripts/verify_engine_mirror.py  (confirm C++ book matches Binance)")
    print("=" * 64)


if __name__ == "__main__":
    main()
