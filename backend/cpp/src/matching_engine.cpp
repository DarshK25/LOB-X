#include "lobx/matching_engine.hpp"

namespace lobx {

bool MatchingEngine::validate(const Order& order) const noexcept {
    if (order.qty == 0)   return false;
    if (order.price <= 0) return false;
    return true;
}

std::vector<Trade> MatchingEngine::submit(Order order) {
    if (!validate(order)) {
        ++stats_.orders_rejected;
        return {};
    }

    ++stats_.orders_accepted;
    auto trades = book_.add_limit_order(std::move(order));
    record_trades(trades);
    return trades;
}

bool MatchingEngine::cancel(OrderId id) {
    bool ok = book_.cancel(id);
    if (ok) ++stats_.orders_cancelled;
    return ok;
}

void MatchingEngine::record_trades(const std::vector<Trade>& trades) {
    for (const auto& t : trades) {
        ++stats_.trades_executed;
        stats_.total_volume += t.qty;
        if (trade_callback_) trade_callback_(t);
    }
}

} // namespace lobx
