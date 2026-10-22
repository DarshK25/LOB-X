#!/usr/bin/env python3
"""Build the C++ matching engine extension (lobx_cpp) and optionally its tests.

    python scripts/setup_cpp_build.py            # build lobx_cpp
    python scripts/setup_cpp_build.py --tests    # also build + run the Catch2 tests
    python scripts/setup_cpp_build.py --bench    # also build + run the native benchmark

Uses CMake + the platform compiler when both are on PATH. Otherwise falls back
to Zig's bundled clang (``pip install ziglang``), which needs no Visual Studio
install: the extension only talks to Python through the C API, so a
clang/libc++ build loads fine into the MSVC-built CPython.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import sysconfig
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CPP = ROOT / "backend" / "cpp"
OUT_DIR = ROOT / "backend" / "python"
ZIG_BUILD = ROOT / "build" / "zig"
CATCH_URL = "https://raw.githubusercontent.com/catchorg/Catch2/v3.5.2/extras/catch_amalgamated.{}"
SOURCES = [CPP / "src" / "order_book.cpp", CPP / "src" / "matching_engine.cpp"]
TESTS = sorted((CPP / "tests").glob("test_*.cpp"))


def run(cmd: list, **kw) -> None:
    print("  $", " ".join(str(c) for c in cmd))
    subprocess.run([str(c) for c in cmd], check=True, **kw)


def ensure(pkg: str, module: str | None = None) -> None:
    try:
        __import__(module or pkg)
    except ImportError:
        run([sys.executable, "-m", "pip", "install", "-q", pkg])


def build_cmake(tests: bool) -> None:
    import pybind11

    build = CPP / "build"
    run(["cmake", "-S", CPP, "-B", build, f"-Dpybind11_DIR={pybind11.get_cmake_dir()}",
         f"-DPython_EXECUTABLE={sys.executable}", "-DCMAKE_BUILD_TYPE=Release"])
    run(["cmake", "--build", build, "--config", "Release", "--parallel"])
    for pyd in list(build.rglob("lobx_cpp*.pyd")) + list(build.rglob("lobx_cpp*.so")):
        shutil.copy2(pyd, OUT_DIR / pyd.name)
    if tests:
        run(["ctest", "--test-dir", build, "-C", "Release", "--output-on-failure"])


def build_zig(tests: bool) -> None:
    ensure("ziglang")
    import pybind11

    zig = [sys.executable, "-m", "ziglang", "c++", "-std=c++20", "-O2", "-w"]
    inc = ["-I", CPP / "include"]
    py_inc = sysconfig.get_paths()["include"]
    ext = sysconfig.get_config_var("EXT_SUFFIX")
    out = OUT_DIR / f"lobx_cpp{ext}"
    link = []
    if sys.platform == "win32":
        ver = f"python{sys.version_info.major}{sys.version_info.minor}"
        link = ["-L", Path(sys.base_prefix) / "libs", f"-l{ver}"]
    run(zig + ["-shared", "-fPIC", *inc, "-I", py_inc, "-I", pybind11.get_include(),
               CPP / "bindings" / "py_bindings.cpp", *SOURCES, *link, "-o", out])
    if tests:
        ZIG_BUILD.mkdir(parents=True, exist_ok=True)
        shim = ZIG_BUILD / "catch2"
        shim.mkdir(exist_ok=True)
        for ext_ in ("hpp", "cpp"):
            dest = ZIG_BUILD / f"catch_amalgamated.{ext_}"
            if not dest.exists():
                urllib.request.urlretrieve(CATCH_URL.format(ext_), dest)
        for header in ("catch_test_macros.hpp", "catch_approx.hpp"):
            (shim / header).write_text('#include "../catch_amalgamated.hpp"\n')
        obj = ZIG_BUILD / "catch.o"
        if not obj.exists():
            run(zig + ["-c", ZIG_BUILD / "catch_amalgamated.cpp", "-I", ZIG_BUILD, "-o", obj])
        exe = ZIG_BUILD / ("lobx_tests.exe" if sys.platform == "win32" else "lobx_tests")
        run(zig + [*inc, "-I", ZIG_BUILD, *SOURCES, *TESTS, obj, "-o", exe])
        run([exe])


def build_bench() -> None:
    """Native order-book benchmark, no Python in the loop (always built with Zig, -O3)."""
    ensure("ziglang")
    ZIG_BUILD.mkdir(parents=True, exist_ok=True)
    exe = ZIG_BUILD / ("bench_orderbook.exe" if sys.platform == "win32" else "bench_orderbook")
    run([sys.executable, "-m", "ziglang", "c++", "-std=c++20", "-O3", "-DNDEBUG", "-w",
         "-I", CPP / "include", *SOURCES, CPP / "bench" / "bench_orderbook.cpp", "-o", exe])
    run([exe])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tests", action="store_true", help="build and run the C++ unit tests")
    ap.add_argument("--zig", action="store_true", help="force the Zig toolchain")
    ap.add_argument("--bench", action="store_true", help="build and run the native benchmark")
    args = ap.parse_args()
    ensure("pybind11")
    use_cmake = not args.zig and shutil.which("cmake") and (
        shutil.which("cl") or shutil.which("c++") or shutil.which("g++"))
    print(f"Building lobx_cpp with {'CMake' if use_cmake else 'Zig (clang)'}")
    (build_cmake if use_cmake else build_zig)(args.tests)
    run([sys.executable, "-c", "import lobx_cpp as c; b = c.OrderBook(); "
         "b.add_limit_order(c.Order(1, 100, 3, False)); "
         "t = b.add_limit_order(c.Order(2, 101, 10, True, c.TimeInForce.FOK)); "
         "assert not t and b.total_ask_qty() == 3; print('lobx_cpp OK (FOK precheck verified)')"],
        cwd=OUT_DIR)
    if args.bench:
        build_bench()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
