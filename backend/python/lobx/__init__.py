"""
LOB-X Python package.

Import order matters: lobx_cpp (C++ bindings) must be importable before
any sub-module that depends on it.  If the pybind11 module hasn't been
built yet, we raise a clear ImportError rather than a cryptic AttributeError.
"""

try:
    import lobx_cpp  # noqa: F401  — the C++ matching engine
except ModuleNotFoundError as exc:
    raise ImportError(
        "lobx_cpp not found. Build the C++ engine first:\n"
        "  cd backend/cpp && cmake -B build && cmake --build build"
    ) from exc

from lobx.__version__ import __version__

__all__ = ["__version__", "lobx_cpp"]
