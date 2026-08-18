"""
LOB-X Python package.

The C++ matching engine (lobx_cpp) is an optional dependency at the package
level — pure-Python sub-modules (market_data, storage, simulation) can be
imported and tested without building the C++ extension.

Sub-modules that specifically need the engine (engine_mirror, strategies)
import lobx_cpp directly and raise a clear error at call-time if it's missing.

LOBX_CPP_AVAILABLE: bool — True once the pybind11 module is built and importable.
"""

try:
    import lobx_cpp  # noqa: F401  — the C++ matching engine
    LOBX_CPP_AVAILABLE: bool = True
except ModuleNotFoundError:
    LOBX_CPP_AVAILABLE = False

from lobx.__version__ import __version__

__all__ = ["__version__", "LOBX_CPP_AVAILABLE"]
