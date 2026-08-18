#pragma once
#include "order.hpp"
#include <deque>
#include <optional>

namespace lobx {

/// A single price level in the order book.
/// Orders within a level are kept in price-time (FIFO) priority.
class PriceLevel {
public:
    void add(Order order) {
        total_qty_ += order.qty_remaining;
        orders_.push_back(std::move(order));
    }

    /// Peek at the front-of-queue order (oldest resting order).
    std::optional<std::reference_wrapper<Order>> front() {
        if (orders_.empty()) return std::nullopt;
        return orders_.front();
    }

    /// Remove the front order (called after a fill).
    void pop_front() {
        if (!orders_.empty()) {
            total_qty_ -= orders_.front().qty_remaining;
            orders_.pop_front();
        }
    }

    /// Reduce qty on front order by `filled`; pop if fully filled.
    void reduce_front(Quantity filled) {
        if (orders_.empty()) return;
        auto& o = orders_.front();
        o.qty_remaining -= filled;
        total_qty_      -= filled;
        if (o.qty_remaining == 0) orders_.pop_front();
    }

    /// Remove an order by id; returns true if found.
    bool remove(OrderId id) {
        for (auto it = orders_.begin(); it != orders_.end(); ++it) {
            if (it->id == id) {
                total_qty_ -= it->qty_remaining;
                orders_.erase(it);
                return true;
            }
        }
        return false;
    }

    bool    empty()     const noexcept { return orders_.empty(); }
    size_t  size()      const noexcept { return orders_.size(); }
    Quantity total_qty() const noexcept { return total_qty_; }

private:
    std::deque<Order> orders_;
    Quantity total_qty_{0};
};

} // namespace lobx
