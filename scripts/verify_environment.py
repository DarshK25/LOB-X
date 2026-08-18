"""
verify_environment.py — check all Phase 1 + Phase 2 prerequisites in one pass.

Run this on first setup and after any environment change:
  python scripts/verify_environment.py

Exit codes: 0 = all OK, 1 = one or more checks failed.
"""
import importlib
import platform
import shutil
import socket
import sys
from pathlib import Path

sys.path.insert(0, "backend/python")

if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

REQUIRED_PACKAGES = [
    "websockets", "httpx", "sortedcontainers",
    "pyarrow", "pandas", "numpy", "scipy",
]

CHECKS_PASSED: list[str] = []
CHECKS_FAILED: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    status = "OK  " if ok else "FAIL"
    detail_str = f"  -- {detail}" if detail else ""
    print(f"  [{status}]  {name}{detail_str}")
    (CHECKS_PASSED if ok else CHECKS_FAILED).append(name)


def check_python_version() -> None:
    v = sys.version_info
    ok = v >= (3, 11)
    check("Python >= 3.11", ok, f"found {platform.python_version()}")


def check_tool(tool: str, friendly_name: str | None = None, warn_only: bool = False) -> None:
    name = friendly_name or f"'{tool}' on PATH"
    found = shutil.which(tool) is not None
    if not found and warn_only:
        print(f"  [WARN]  {name}  -- not on PATH (OK if lobx_cpp already built via vswhere)")
        CHECKS_PASSED.append(name + " [warn-only]")
    else:
        check(name, found)


def check_packages() -> None:
    for pkg in REQUIRED_PACKAGES:
        try:
            importlib.import_module(pkg)
            check(f"import {pkg}", True)
        except ImportError as e:
            check(f"import {pkg}", False, str(e))


def check_lobx_package() -> None:
    try:
        import lobx  # noqa: F401
        check("import lobx (Python package)", True)
    except ImportError as e:
        check("import lobx (Python package)", False,
              f"{e} — install with: pip install -e backend/python")


def check_lobx_cpp() -> None:
    try:
        import lobx_cpp  # noqa: F401
        check("import lobx_cpp (C++ engine)", True)
    except ImportError as e:
        check("import lobx_cpp (C++ engine)", False,
              f"{e} — run: python scripts/setup_cpp_build.py")


def check_strategy_imports() -> None:
    try:
        from lobx.strategy.base import MarketState, Strategy          # noqa: F401
        from lobx.strategy.fixed_spread import FixedSpreadStrategy    # noqa: F401
        from lobx.strategy.avellaneda import AvellanedaStoikovStrategy  # noqa: F401
        check("Phase 2 strategy imports", True)
    except ImportError as e:
        check("Phase 2 strategy imports", False, str(e))


def check_execution_imports() -> None:
    try:
        from lobx.execution.inventory import InventoryTracker         # noqa: F401
        from lobx.execution.shadow_engine import ShadowQuoteEngine    # noqa: F401
        check("Phase 2 execution imports", True)
    except ImportError as e:
        check("Phase 2 execution imports", False, str(e))


def check_analytics_imports() -> None:
    try:
        from lobx.analytics.volatility import EWMAVolatilityEstimator  # noqa: F401
        check("Phase 2 analytics imports", True)
    except ImportError as e:
        check("Phase 2 analytics imports", False, str(e))


def check_data_dir_writable() -> None:
    p = Path("data/raw")
    try:
        p.mkdir(parents=True, exist_ok=True)
        f = p / ".write_test"
        f.write_text("ok")
        f.unlink()
        check("data/raw/ is writable", True)
    except OSError as e:
        check("data/raw/ is writable", False, str(e))


def check_binance_reachable() -> None:
    try:
        sock = socket.create_connection(("data-stream.binance.vision", 443), timeout=5)
        sock.close()
        check("Binance WebSocket host reachable", True)
    except OSError as e:
        check("Binance WebSocket host reachable", False, str(e))


def check_collector_hooks() -> None:
    try:
        from lobx.market_data.collector import Collector
        c = Collector(enable_engine_mirror=False)
        ok = hasattr(c, "on_book_update") and hasattr(c, "on_trade")
        check("Collector has on_book_update/on_trade hooks", ok)
    except Exception as e:
        check("Collector has on_book_update/on_trade hooks", False, str(e))


if __name__ == "__main__":
    print("\n  LOB-X Environment Check")
    print("  " + "=" * 50)
    print("  System")
    print("  " + "-" * 50)
    check_python_version()
    check_tool("git")
    check_tool("cmake")
    if platform.system() == "Windows":
        check_tool("cl", "C++ compiler ('cl') on PATH",
                   warn_only=True)   # MSVC found via vswhere; cl not on PATH is normal
    else:
        check_tool("gcc", "C++ compiler ('gcc') on PATH")

    print("\n  Python packages")
    print("  " + "-" * 50)
    check_packages()

    print("\n  lobx modules")
    print("  " + "-" * 50)
    check_lobx_package()
    check_lobx_cpp()
    check_strategy_imports()
    check_execution_imports()
    check_analytics_imports()
    check_collector_hooks()

    print("\n  Infrastructure")
    print("  " + "-" * 50)
    check_data_dir_writable()
    check_binance_reachable()

    print("\n  " + "=" * 50)
    print(f"  {len(CHECKS_PASSED)} passed, {len(CHECKS_FAILED)} failed.")
    if CHECKS_FAILED:
        print(f"  Failed checks: {', '.join(CHECKS_FAILED)}")
        print("  Fix the items above before running the collector or strategy.")
        sys.exit(1)
    else:
        print("  Environment looks good. Ready to run.")
    print()
