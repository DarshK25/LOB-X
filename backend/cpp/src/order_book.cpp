#include "lobx/order_book.hpp"
#include <stdexcept>

namespace lobx {

// ── Matching ──────────────────────────────────────────────────────────────────

template <typename PassiveMap>
std::vector<Trade> OrderBook::match(Order& aggressor, PassiveMap& passive_side) {
    std::vector<Trade> trades;

    while (aggressor.qty_remaining > 0 && !passive_side.empty()) {
        auto& [passive_price, level] = *passive_side.begin();

        // Price check: buy aggressor must pay at least passive ask;
        // sell aggressor must accept at most passive bid.
        if (aggressor.is_buy()) {
            if (aggressor.price < passive_price) break;
        } else {
            if (aggressor.price > passive_price) break;
        }

        // Fill against each resting order in the level (FIFO).
        while (aggressor.qty_remaining > 0 && !level.empty()) {
            // Copy what we need: reduce_front() pops a fully filled order,
            // which invalidates any reference to it.
            const Order& passive = level.front().value().get();
            const OrderId passive_id = passive.id;
            const Quantity fill_qty = std::min(aggressor.qty_remaining, passive.qty_remaining);
            const bool passive_done = fill_qty == passive.qty_remaining;

            OrderId buy_id  = aggressor.is_buy()  ? aggressor.id : passive_id;
            OrderId sell_id = aggressor.is_sell() ? aggressor.id : passive_id;
            trades.emplace_back(buy_id, sell_id, passive_price, fill_qty);

            aggressor.qty_remaining -= fill_qty;
            level.reduce_front(fill_qty);
            if (passive_done) order_index_.erase(passive_id);
        }

        if (level.empty()) passive_side.erase(passive_side.begin());
    }

    return trades;
}

template <typename PassiveMap>
Quantity OrderBook::fillable_qty(const Order& aggressor, const PassiveMap& passive_side) const {
    Quantity available = 0;
    for (const auto& [price, level] : passive_side) {
        if (aggressor.is_buy() ? aggressor.price < price : aggressor.price > price) break;
        available += level.total_qty();
        if (available >= aggressor.qty_remaining) break;
    }
    return available;
}

std::vector<Trade> OrderBook::add_limit_order(Order order) {
    // FOK must be decided before any trade happens: either the whole quantity
    // is available at acceptable prices, or nothing executes.
    if (order.tif == TimeInForce::FOK) {
        const Quantity available = order.is_buy() ? fillable_qty(order, asks_)
                                                  : fillable_qty(order, bids_);
        if (available < order.qty_remaining) return {};
    }

    std::vector<Trade> trades = order.is_buy() ? match(order, asks_) : match(order, bids_);

    // IOC and FOK never rest.
    if (order.tif == TimeInForce::IOC || order.tif == TimeInForce::FOK) return trades;

    if (order.qty_remaining > 0) rest_order(std::move(order));
    return trades;
}

void OrderBook::rest_order(Order order) {
    const Price price = order.price;
    const Side side = order.side;
    const OrderId id = order.id;
    auto it = order.is_buy() ? bids_[price].add(std::move(order))
                             : asks_[price].add(std::move(order));
    order_index_[id] = Location{price, side, it};
}

bool OrderBook::cancel(OrderId id) {
    auto found = order_index_.find(id);
    if (found == order_index_.end()) return false;
    const Location loc = found->second;
    order_index_.erase(found);

    auto erase_from = [&](auto& side_map) {
        auto level = side_map.find(loc.price);
        level->second.erase(loc.it);
        if (level->second.empty()) side_map.erase(level);
    };
    if (loc.side == Side::Buy) erase_from(bids_);
    else erase_from(asks_);
    return true;
}

// ── Queries ───────────────────────────────────────────────────────────────────

std::optional<Price> OrderBook::best_bid() const {
    if (bids_.empty()) return std::nullopt;
    return bids_.begin()->first;
}

std::optional<Quantity> OrderBook::best_bid_qty() const {
    if (bids_.empty()) return std::nullopt;
    return bids_.begin()->second.total_qty();
}

std::optional<Price> OrderBook::best_ask() const {
    if (asks_.empty()) return std::nullopt;
    return asks_.begin()->first;
}

std::optional<Quantity> OrderBook::best_ask_qty() const {
    if (asks_.empty()) return std::nullopt;
    return asks_.begin()->second.total_qty();
}

std::optional<double> OrderBook::mid_price() const {
    auto bid = best_bid();
    auto ask = best_ask();
    if (!bid || !ask) return std::nullopt;
    return static_cast<double>(*bid + *ask) / 2.0;
}

std::optional<Price> OrderBook::spread() const {
    auto bid = best_bid();
    auto ask = best_ask();
    if (!bid || !ask) return std::nullopt;
    return *ask - *bid;
}

Quantity OrderBook::total_bid_qty() const {
    Quantity total = 0;
    for (const auto& [price, level] : bids_) total += level.total_qty();
    return total;
}

Quantity OrderBook::total_ask_qty() const {
    Quantity total = 0;
    for (const auto& [price, level] : asks_) total += level.total_qty();
    return total;
}

} // namespace lobx
