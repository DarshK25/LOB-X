"""
inspect_as_module.py — print the real function signatures in
lobx.models.avellaneda so strategy/avellaneda.py can import the right names.

Run before creating any strategy code:
  python scripts/inspect_as_module.py
"""
import sys
import inspect

sys.path.insert(0, "backend/python")

print("=" * 60)
print("  lobx.models.avellaneda — package contents")
print("=" * 60)
import lobx.models.avellaneda as as_pkg
print(f"File  : {as_pkg.__file__}")
print(f"Exports: {[x for x in dir(as_pkg) if not x.startswith('_')]}\n")

print("=" * 60)
print("  Scanning sub-modules")
print("=" * 60)

submodules = [
    "reservation_price",
    "optimal_spread",
    "market_maker",
]

for sub in submodules:
    mod_name = f"lobx.models.avellaneda.{sub}"
    try:
        m = __import__(mod_name, fromlist=[sub])
        print(f"\n--- {mod_name} ---")
        for name in sorted(dir(m)):
            attr = getattr(m, name)
            if name.startswith("_"):
                continue
            if callable(attr):
                try:
                    sig = inspect.signature(attr)
                    print(f"  {name}{sig}")
                except (TypeError, ValueError):
                    print(f"  {name} (no signature)")
            elif not inspect.ismodule(attr):
                print(f"  {name} = {attr!r}")
    except ImportError as e:
        print(f"  [SKIP] {mod_name}: {e}")

print("\n" + "=" * 60)
print("  Key facts for strategy/avellaneda.py")
print("=" * 60)
from lobx.models.avellaneda.reservation_price import reservation_price
from lobx.models.avellaneda.optimal_spread import optimal_spread, half_spread

print("\nreservation_price signature:")
print(f"  {inspect.signature(reservation_price)}")
print("\noptimal_spread signature:")
print(f"  {inspect.signature(optimal_spread)}")
print("\nhalf_spread signature:")
print(f"  {inspect.signature(half_spread)}")

print("\n[DONE] Use the above names/args exactly in strategy/avellaneda.py")
