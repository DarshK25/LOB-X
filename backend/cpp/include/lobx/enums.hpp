#pragma once
#include <cstdint>

namespace lobx {

/// Strongly-typed aliases — avoids mixing up price / qty / id by accident.
using OrderId  = std::uint64_t;
using Price    = std::int64_t;   ///< ticks (e.g., cents × 100 for sub-cent)
using Quantity = std::uint64_t;  ///< lots

enum class Side : std::uint8_t {
    Buy  = 0,
    Sell = 1,
};

enum class OrderStatus : std::uint8_t {
    New       = 0,
    PartFill  = 1,
    Filled    = 2,
    Cancelled = 3,
    Rejected  = 4,
};

enum class TimeInForce : std::uint8_t {
    GTC = 0,  ///< Good-Till-Cancelled (default)
    IOC = 1,  ///< Immediate-Or-Cancel
    FOK = 2,  ///< Fill-Or-Kill
};

} // namespace lobx
