#pragma once
#include "enums.hpp"
#include <chrono>
#include <cstdint>

namespace lobx {

struct Trade {
    OrderId  buy_id;
    OrderId  sell_id;
    Price    price;
    Quantity qty;
    std::chrono::steady_clock::time_point timestamp;

    Trade(OrderId b, OrderId s, Price p, Quantity q)
        : buy_id(b), sell_id(s), price(p), qty(q)
        , timestamp(std::chrono::steady_clock::now())
    {}
};

} // namespace lobx
