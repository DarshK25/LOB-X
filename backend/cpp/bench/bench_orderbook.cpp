// Native order-book benchmark (no Python in the loop).
//
// Workload: limit orders around a mid of 10'000 ticks (+-20), 20% cancels of
// recent orders, so the book keeps ~40 active levels and orders cross often.
// Reports throughput and per-call latency percentiles of add_limit_order and
// cancel, measured with steady_clock around each call.
#include "lobx/order_book.hpp"

#include <algorithm>
#include <chrono>
#include <cstdio>
#include <deque>
#include <random>
#include <vector>

using namespace lobx;
using Clock = std::chrono::steady_clock;

static double pct(std::vector<long long>& v, double p) {
    if (v.empty()) return 0.0;
    std::size_t k = static_cast<std::size_t>(p * (v.size() - 1));
    std::nth_element(v.begin(), v.begin() + k, v.end());
    return static_cast<double>(v[k]);
}

int main() {
    constexpr int N = 2'000'000;
    std::mt19937_64 rng(42);
    std::uniform_int_distribution<int> px(-20, 20), qty(1, 20);
    std::uniform_real_distribution<double> u(0.0, 1.0);

    OrderBook book;
    std::deque<OrderId> recent;
    std::vector<long long> add_ns, cancel_ns;
    add_ns.reserve(N);
    cancel_ns.reserve(N / 4);
    std::size_t trades = 0;

    const auto t0 = Clock::now();
    for (OrderId id = 1; id <= static_cast<OrderId>(N); ++id) {
        if (!recent.empty() && u(rng) < 0.2) {
            const OrderId victim = recent[static_cast<std::size_t>(u(rng) * recent.size())];
            const auto a = Clock::now();
            book.cancel(victim);
            cancel_ns.push_back(std::chrono::duration_cast<std::chrono::nanoseconds>(Clock::now() - a).count());
            continue;
        }
        Order o(id, 10'000 + px(rng), qty(rng), u(rng) < 0.5);
        const auto a = Clock::now();
        trades += book.add_limit_order(std::move(o)).size();
        add_ns.push_back(std::chrono::duration_cast<std::chrono::nanoseconds>(Clock::now() - a).count());
        recent.push_back(id);
        if (recent.size() > 500) recent.pop_front();
    }
    const double secs = std::chrono::duration<double>(Clock::now() - t0).count();
    const std::size_t ops = add_ns.size() + cancel_ns.size();

    std::printf("ops %zu in %.3f s -> %.2f M ops/s (%zu trades, %zu bid / %zu ask levels left)\n",
                ops, secs, ops / secs / 1e6, trades, book.bid_levels(), book.ask_levels());
    std::printf("add_limit_order ns: p50 %.0f  p90 %.0f  p99 %.0f  p99.9 %.0f\n",
                pct(add_ns, 0.5), pct(add_ns, 0.9), pct(add_ns, 0.99), pct(add_ns, 0.999));
    std::printf("cancel ns:          p50 %.0f  p90 %.0f  p99 %.0f  p99.9 %.0f\n",
                pct(cancel_ns, 0.5), pct(cancel_ns, 0.9), pct(cancel_ns, 0.99), pct(cancel_ns, 0.999));
    return 0;
}
