cmake_minimum_required(VERSION 3.20)
project(lobx_engine LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_EXPORT_COMPILE_COMMANDS ON)

# ─── Core static library ──────────────────────────────────────────────────────
add_library(lobx_core STATIC
    src/order_book.cpp
    src/matching_engine.cpp
)
target_include_directories(lobx_core PUBLIC include)
target_compile_options(lobx_core PRIVATE -O3 -Wall -Wextra)

# ─── Catch2 tests ─────────────────────────────────────────────────────────────
include(FetchContent)
FetchContent_Declare(
    catch2
    URL https://github.com/catchorg/Catch2/archive/refs/tags/v3.5.2.tar.gz
)
FetchContent_MakeAvailable(catch2)

add_executable(lobx_tests
    tests/test_orderbook.cpp
    tests/test_matching.cpp
    tests/test_cancel.cpp
)
target_link_libraries(lobx_tests PRIVATE lobx_core Catch2::Catch2WithMain)

include(CTest)
include(Catch)
catch_discover_tests(lobx_tests)

# ─── pybind11 bindings ────────────────────────────────────────────────────────
find_package(pybind11 CONFIG QUIET)
if(pybind11_FOUND)
    pybind11_add_module(lobx_cpp bindings/py_bindings.cpp)
    target_link_libraries(lobx_cpp PRIVATE lobx_core)
    message(STATUS "pybind11 found — Python bindings will be built")
else()
    message(WARNING "pybind11 not found — skipping Python bindings. "
                    "Install with: pip install pybind11")
endif()
