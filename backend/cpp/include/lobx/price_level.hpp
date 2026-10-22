#pragma once
#include "order.hpp"
#include <list>
#include <optional>

namespace lobx {

/// One price level: resting orders in time priority (FIFO).
///
/// std::list keeps iterators stable, so the book can store an iterator per
/// order id and cancel in O(1) instead of scanning the level.
class PriceLevel {
public:
    using Iterator = std::list<Order>::iterator;

    Iterator add(Order order) {
        total_qty_ += order.qty_remaining;
        orders_.push_back(std::move(order));
        return std::prev(orders_.end());
    }

    std::optional<std::reference_wrapper<Order>> front() {
        if (orders_.empty()) return std::nullopt;
        return orders_.front();
    }

    /// Reduce the front order by `filled`; pops it when fully filled, which
    /// invalidates references to it.
    void reduce_front(Quantity filled) {
        if (orders_.empty()) return;
        auto& o = orders_.front();
        o.qty_remaining -= filled;
        total_qty_      -= filled;
        if (o.qty_remaining == 0) orders_.pop_front();
    }

    void erase(Iterator it) {
        total_qty_ -= it->qty_remaining;
        orders_.erase(it);
    }

    bool     empty()     const noexcept { return orders_.empty(); }
    size_t   size()      const noexcept { return orders_.size(); }
    Quantity total_qty() const noexcept { return total_qty_; }

private:
    std::list<Order> orders_;
    Quantity total_qty_{0};
};

} // namespace lobx
