"""
LOB-X Python package.

The C++ matching engine extension (lobx_cpp) is optional: only the API and the
collector's engine mirror need it. Models, the execution simulator, backtests
and the terminal run without it.

LOBX_CPP_AVAILABLE: bool — True once the pybind11 module is built and importable.
"""
from pathlib import Path

# Automatically load the .env file from the project root on import
try:
    from dotenv import load_dotenv
    env_path = Path(__file__).resolve().parents[3] / ".env"
    load_dotenv(dotenv_path=env_path)
except ImportError:
    pass

try:
    import lobx_cpp  # noqa: F401  — the C++ matching engine
    LOBX_CPP_AVAILABLE: bool = True
except ModuleNotFoundError:
    LOBX_CPP_AVAILABLE = False

from lobx.__version__ import __version__

__all__ = ["__version__", "LOBX_CPP_AVAILABLE"]

