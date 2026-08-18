#include <catch2/catch_test_macros.hpp>
#include <catch2/catch_approx.hpp>
#include "lobx/order_book.hpp"

using namespace lobx;
using Catch::Approx;

static OrderId next_id() {
    static OrderId n = 1;
    return n++;
}

static Order buy(Price price, Quantity qty) {
    return Order{next_id(), price, qty, true};
}

static Order sell(Price price, Quantity qty) {
    return Order{next_id(), price, qty, false};
}

TEST_CASE("Empty book has no best bid/ask", "[orderbook]") {
    OrderBook book;
    REQUIRE_FALSE(book.best_bid().has_value());
    REQUIRE_FALSE(book.best_ask().has_value());
    REQUIRE(book.empty());
}

TEST_CASE("Resting bid updates best_bid", "[orderbook]") {
    OrderBook book;
    book.add_limit_order(buy(100, 10));
    REQUIRE(book.best_bid().has_value());
    REQUIRE(*book.best_bid() == 100);
    REQUIRE_FALSE(book.best_ask().has_value());
}

TEST_CASE("Resting ask updates best_ask", "[orderbook]") {
    OrderBook book;
    book.add_limit_order(sell(102, 5));
    REQUIRE(book.best_ask().has_value());
    REQUIRE(*book.best_ask() == 102);
    REQUIRE_FALSE(book.best_bid().has_value());
}

TEST_CASE("Multiple bids: best_bid is highest", "[orderbook]") {
    OrderBook book;
    book.add_limit_order(buy(100, 10));
    book.add_limit_order(buy(101, 5));
    book.add_limit_order(buy(99,  8));
    REQUIRE(book.best_bid().has_value());
    REQUIRE(*book.best_bid() == 101);
    REQUIRE(book.bid_levels() == 3);
}

TEST_CASE("Multiple asks: best_ask is lowest", "[orderbook]") {
    OrderBook book;
    book.add_limit_order(sell(102, 5));
    book.add_limit_order(sell(103, 3));
    book.add_limit_order(sell(101, 7));
    REQUIRE(book.best_ask().has_value());
    REQUIRE(*book.best_ask() == 101);
    REQUIRE(book.ask_levels() == 3);
}

TEST_CASE("Spread and mid price", "[orderbook]") {
    OrderBook book;
    book.add_limit_order(buy(100, 10));
    book.add_limit_order(sell(102, 10));
    REQUIRE(book.spread().has_value());
    REQUIRE(*book.spread() == 2);
    REQUIRE(book.mid_price().has_value());
    REQUIRE(*book.mid_price() == Approx(101.0));
}

TEST_CASE("Total qty tracks resting orders", "[orderbook]") {
    OrderBook book;
    book.add_limit_order(buy(100, 10));
    book.add_limit_order(buy(100, 5));
    book.add_limit_order(buy(99,  8));
    REQUIRE(book.total_bid_qty() == 23);
    REQUIRE(book.total_ask_qty() == 0);
}
