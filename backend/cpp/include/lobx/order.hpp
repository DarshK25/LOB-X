#pragma once
#include "enums.hpp"
#include <chrono>
#include <cstdint>

namespace lobx {

struct Order {
    OrderId  id;
    Price    price;
    Quantity qty;
    Quantity qty_remaining;
    Side     side;
    TimeInForce tif;
    std::chrono::steady_clock::time_point timestamp;

    /// Convenience constructor used by pybind11 bindings and tests.
    /// `is_buy` maps to Side::Buy/Sell for Python-facing API.
    Order(OrderId id_, Price price_, Quantity qty_, bool is_buy,
          TimeInForce tif_ = TimeInForce::GTC)
        : id(id_)
        , price(price_)
        , qty(qty_)
        , qty_remaining(qty_)
        , side(is_buy ? Side::Buy : Side::Sell)
        , tif(tif_)
        , timestamp(std::chrono::steady_clock::now())
    {}

    bool is_buy()  const noexcept { return side == Side::Buy;  }
    bool is_sell() const noexcept { return side == Side::Sell; }
    bool is_filled() const noexcept { return qty_remaining == 0; }
};

} // namespace lobx
