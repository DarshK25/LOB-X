#include <catch2/catch_test_macros.hpp>
#include "lobx/order_book.hpp"

using namespace lobx;

static OrderId cid = 5000;
static Order buy(Price p, Quantity q)  { return Order{cid++, p, q, true};  }
static Order sell(Price p, Quantity q) { return Order{cid++, p, q, false}; }

TEST_CASE("Cancel a resting bid", "[cancel]") {
    OrderBook book;
    OrderId id = cid;
    book.add_limit_order(buy(100, 10));
    REQUIRE(book.best_bid() == 100);
    REQUIRE(book.cancel(id));
    REQUIRE_FALSE(book.best_bid().has_value());
    REQUIRE(book.empty());
}

TEST_CASE("Cancel a resting ask", "[cancel]") {
    OrderBook book;
    OrderId id = cid;
    book.add_limit_order(sell(102, 5));
    REQUIRE(book.cancel(id));
    REQUIRE_FALSE(book.best_ask().has_value());
}

TEST_CASE("Cancel non-existent order returns false", "[cancel]") {
    OrderBook book;
    REQUIRE_FALSE(book.cancel(99999));
}

TEST_CASE("Cancel one of two orders at same level", "[cancel]") {
    OrderBook book;
    OrderId first  = cid;
    book.add_limit_order(buy(100, 5));
    OrderId second = cid;
    book.add_limit_order(buy(100, 8));

    REQUIRE(book.cancel(first));
    REQUIRE(book.total_bid_qty() == 8);
    REQUIRE(book.bid_levels() == 1);
}

TEST_CASE("Cancel removes level entirely when last order cancelled", "[cancel]") {
    OrderBook book;
    book.add_limit_order(buy(100, 10));
    book.add_limit_order(buy(101, 5));

    // Cancel the 100-level order
    OrderId id_100 = cid - 2;  // first of the two
    // rebuild with fresh ids to be deterministic
    OrderBook book2;
    OrderId a = cid; book2.add_limit_order(buy(101, 5));
    OrderId b = cid; book2.add_limit_order(buy(100, 10));
    REQUIRE(book2.bid_levels() == 2);
    REQUIRE(book2.cancel(b));
    REQUIRE(book2.bid_levels() == 1);
    REQUIRE(book2.best_bid() == 101);
}

TEST_CASE("Cancel after partial fill: only remaining qty cancelled", "[cancel]") {
    OrderBook book;
    OrderId bid_id = cid;
    book.add_limit_order(buy(100, 10));
    book.add_limit_order(sell(100, 4));    // partial fill: 6 remaining
    REQUIRE(book.total_bid_qty() == 6);
    REQUIRE(book.cancel(bid_id));
    REQUIRE(book.empty());
}

TEST_CASE("Double cancel returns false on second attempt", "[cancel]") {
    OrderBook book;
    OrderId id = cid;
    book.add_limit_order(buy(100, 10));
    REQUIRE(book.cancel(id));
    REQUIRE_FALSE(book.cancel(id));
}
