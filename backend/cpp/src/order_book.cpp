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
            auto& passive = level.front().value().get();
            Quantity fill_qty = std::min(aggressor.qty_remaining, passive.qty_remaining);
            Price    fill_prc = passive_price;  // passive price wins

            OrderId buy_id  = aggressor.is_buy()  ? aggressor.id : passive.id;
            OrderId sell_id = aggressor.is_sell() ? aggressor.id : passive.id;
            trades.emplace_back(buy_id, sell_id, fill_prc, fill_qty);

            aggressor.qty_remaining -= fill_qty;
            level.reduce_front(fill_qty);

            // Remove the order_index entry for a fully-filled passive order.
            if (passive.qty_remaining == 0) {
                order_index_.erase(passive.id);
            }
        }

        if (level.empty()) passive_side.erase(passive_side.begin());
    }

    return trades;
}

std::vector<Trade> OrderBook::add_limit_order(Order order) {
    std::vector<Trade> trades;

    if (order.is_buy()) {
        trades = match(order, asks_);
    } else {
        trades = match(order, bids_);
    }

    // IOC: cancel unfilled remainder immediately.
    if (order.tif == TimeInForce::IOC && order.qty_remaining > 0) {
        return trades;
    }

    // FOK: if not fully filled, discard entirely (already removed from book).
    if (order.tif == TimeInForce::FOK) {
        // If we generated any partial trades we must not rest — just return
        // what we matched. Caller can detect partial fill by inspecting trades.
        return trades;
    }

    // Rest the remaining qty.
    if (order.qty_remaining > 0) {
        rest_order(std::move(order));
    }

    return trades;
}

void OrderBook::rest_order(Order order) {
    Price price = order.price;
    order_index_[order.id] = price;

    if (order.is_buy()) {
        bids_[price].add(std::move(order));
    } else {
        asks_[price].add(std::move(order));
    }
}

bool OrderBook::cancel(OrderId id) {
    auto it = order_index_.find(id);
    if (it == order_index_.end()) return false;

    Price price = it->second;
    order_index_.erase(it);

    // Try bids first, then asks.
    auto bid_it = bids_.find(price);
    if (bid_it != bids_.end()) {
        if (bid_it->second.remove(id)) {
            if (bid_it->second.empty()) bids_.erase(bid_it);
            return true;
        }
    }

    auto ask_it = asks_.find(price);
    if (ask_it != asks_.end()) {
        if (ask_it->second.remove(id)) {
            if (ask_it->second.empty()) asks_.erase(ask_it);
            return true;
        }
    }

    return false;
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
