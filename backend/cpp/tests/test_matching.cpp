#include <catch2/catch_test_macros.hpp>
#include "lobx/order_book.hpp"

using namespace lobx;

static OrderId gid = 1000;
static Order buy(Price p, Quantity q)  { return Order{gid++, p, q, true};  }
static Order sell(Price p, Quantity q) { return Order{gid++, p, q, false}; }

TEST_CASE("No match when spread exists", "[matching]") {
    OrderBook book;
    book.add_limit_order(buy(100, 10));
    auto trades = book.add_limit_order(sell(101, 5));
    REQUIRE(trades.empty());
    REQUIRE(book.best_bid().has_value());
    REQUIRE(*book.best_bid() == 100);
    REQUIRE(book.best_ask().has_value());
    REQUIRE(*book.best_ask() == 101);
}

TEST_CASE("Full fill: exact price cross", "[matching]") {
    OrderBook book;
    book.add_limit_order(buy(100, 10));
    auto trades = book.add_limit_order(sell(100, 10));
    REQUIRE(trades.size() == 1);
    REQUIRE(trades[0].price == 100);
    REQUIRE(trades[0].qty   == 10);
    REQUIRE(book.empty());
}

TEST_CASE("Partial fill: aggressor larger than resting", "[matching]") {
    OrderBook book;
    book.add_limit_order(buy(100, 5));
    auto trades = book.add_limit_order(sell(100, 10));
    REQUIRE(trades.size() == 1);
    REQUIRE(trades[0].qty == 5);
    // Remainder of sell rests at 100
    REQUIRE(book.best_ask().has_value());
    REQUIRE(*book.best_ask() == 100);
    REQUIRE(book.total_ask_qty() == 5);
}

TEST_CASE("Partial fill: passive larger than aggressor", "[matching]") {
    OrderBook book;
    book.add_limit_order(buy(100, 10));
    auto trades = book.add_limit_order(sell(100, 4));
    REQUIRE(trades.size() == 1);
    REQUIRE(trades[0].qty == 4);
    REQUIRE(book.total_bid_qty() == 6);
    REQUIRE_FALSE(book.best_ask().has_value());
}

TEST_CASE("Price-time priority: older order at same level fills first", "[matching]") {
    OrderBook book;
    OrderId first_id  = gid;
    book.add_limit_order(buy(100, 5));   // first
    OrderId second_id = gid;
    book.add_limit_order(buy(100, 5));   // second (same price)

    auto trades = book.add_limit_order(sell(100, 5));
    REQUIRE(trades.size() == 1);
    REQUIRE(trades[0].buy_id == first_id);   // first in wins
    (void)second_id;
}

TEST_CASE("Multi-level sweep: sell walks through bids", "[matching]") {
    OrderBook book;
    book.add_limit_order(buy(102, 3));
    book.add_limit_order(buy(101, 3));
    book.add_limit_order(buy(100, 3));

    auto trades = book.add_limit_order(sell(100, 9));
    REQUIRE(trades.size() == 3);
    REQUIRE(trades[0].price == 102);
    REQUIRE(trades[1].price == 101);
    REQUIRE(trades[2].price == 100);
    REQUIRE(book.empty());
}

TEST_CASE("Trade records correct buy/sell ids", "[matching]") {
    OrderBook book;
    OrderId bid_id = gid;
    book.add_limit_order(buy(100, 10));
    OrderId ask_id = gid;
    auto trades = book.add_limit_order(sell(100, 10));
    REQUIRE(trades[0].buy_id  == bid_id);
    REQUIRE(trades[0].sell_id == ask_id);
}
