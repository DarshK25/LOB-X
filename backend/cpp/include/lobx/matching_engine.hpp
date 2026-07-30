#pragma once
#include "order_book.hpp"
#include "trade.hpp"
#include <cstdint>
#include <functional>
#include <vector>

namespace lobx {

struct EngineStats {
    uint64_t orders_accepted{0};
    uint64_t orders_rejected{0};
    uint64_t orders_cancelled{0};
    uint64_t trades_executed{0};
    uint64_t total_volume{0};   ///< sum of all trade qtys
};

/// Thin orchestration layer on top of OrderBook.
/// Validates orders, enforces limits, fires callbacks, records stats.
class MatchingEngine {
public:
    using TradeCallback = std::function<void(const Trade&)>;

    explicit MatchingEngine() = default;

    /// Register a callback invoked synchronously for every Trade.
    void on_trade(TradeCallback cb) { trade_callback_ = std::move(cb); }

    /// Submit a limit order. Returns generated trades.
    std::vector<Trade> submit(Order order);

    /// Cancel a resting order by id. Returns true if cancelled.
    bool cancel(OrderId id);

    // ── Accessors ─────────────────────────────────────────────────────────────
    const OrderBook&   book()  const noexcept { return book_; }
    const EngineStats& stats() const noexcept { return stats_; }

    std::optional<Price>  best_bid() const { return book_.best_bid(); }
    std::optional<Price>  best_ask() const { return book_.best_ask(); }
    std::optional<double> mid_price() const { return book_.mid_price(); }

private:
    OrderBook    book_;
    EngineStats  stats_;
    TradeCallback trade_callback_;

    bool validate(const Order& order) const noexcept;
    void record_trades(const std::vector<Trade>& trades);
};

} // namespace lobx
