#include <pybind11/pybind11.h>
#include <pybind11/stl.h>
#include <pybind11/functional.h>
#include <pybind11/chrono.h>
#include "lobx/order.hpp"
#include "lobx/trade.hpp"
#include "lobx/order_book.hpp"
#include "lobx/matching_engine.hpp"
#include "lobx/enums.hpp"

namespace py = pybind11;
using namespace lobx;

PYBIND11_MODULE(lobx_cpp, m) {
    m.doc() = "LOB-X C++ matching engine — pybind11 bindings";

    // ── Enums ─────────────────────────────────────────────────────────────────
    py::enum_<Side>(m, "Side")
        .value("Buy",  Side::Buy)
        .value("Sell", Side::Sell)
        .export_values();

    py::enum_<TimeInForce>(m, "TimeInForce")
        .value("GTC", TimeInForce::GTC)
        .value("IOC", TimeInForce::IOC)
        .value("FOK", TimeInForce::FOK)
        .export_values();

    py::enum_<OrderStatus>(m, "OrderStatus")
        .value("New",       OrderStatus::New)
        .value("PartFill",  OrderStatus::PartFill)
        .value("Filled",    OrderStatus::Filled)
        .value("Cancelled", OrderStatus::Cancelled)
        .value("Rejected",  OrderStatus::Rejected)
        .export_values();

    // ── Order ─────────────────────────────────────────────────────────────────
    py::class_<Order>(m, "Order")
        .def(py::init<OrderId, Price, Quantity, bool, TimeInForce>(),
             py::arg("id"), py::arg("price"), py::arg("qty"),
             py::arg("is_buy"), py::arg("tif") = TimeInForce::GTC)
        .def_readwrite("id",            &Order::id)
        .def_readwrite("price",         &Order::price)
        .def_readwrite("qty",           &Order::qty)
        .def_readwrite("qty_remaining", &Order::qty_remaining)
        .def_readwrite("tif",           &Order::tif)
        .def_property_readonly("is_buy",  [](const Order& o){ return o.is_buy();  })
        .def_property_readonly("is_sell", [](const Order& o){ return o.is_sell(); })
        .def_property_readonly("is_filled",[](const Order& o){ return o.is_filled(); })
        .def("__repr__", [](const Order& o) {
            return "<Order id=" + std::to_string(o.id) +
                   " price=" + std::to_string(o.price) +
                   " qty=" + std::to_string(o.qty_remaining) +
                   " side=" + (o.is_buy() ? "Buy" : "Sell") + ">";
        });

    // ── Trade ─────────────────────────────────────────────────────────────────
    py::class_<Trade>(m, "Trade")
        .def_readonly("buy_id",  &Trade::buy_id)
        .def_readonly("sell_id", &Trade::sell_id)
        .def_readonly("price",   &Trade::price)
        .def_readonly("qty",     &Trade::qty)
        .def("__repr__", [](const Trade& t) {
            return "<Trade buy=" + std::to_string(t.buy_id) +
                   " sell=" + std::to_string(t.sell_id) +
                   " px=" + std::to_string(t.price) +
                   " qty=" + std::to_string(t.qty) + ">";
        });

    // ── OrderBook ─────────────────────────────────────────────────────────────
    py::class_<OrderBook>(m, "OrderBook")
        .def(py::init<>())
        .def("add_limit_order", &OrderBook::add_limit_order, py::arg("order"),
             "Submit a limit order. Returns list[Trade] generated.")
        .def("cancel",          &OrderBook::cancel, py::arg("order_id"),
             "Cancel a resting order by id. Returns True if found.")
        .def("best_bid",        &OrderBook::best_bid,
             "Best bid price (int) or None.")
        .def("best_ask",        &OrderBook::best_ask,
             "Best ask price (int) or None.")
        .def("mid_price",       &OrderBook::mid_price,
             "Mid price (float) or None.")
        .def("spread",          &OrderBook::spread,
             "Spread in ticks (int) or None.")
        .def("total_bid_qty",   &OrderBook::total_bid_qty)
        .def("total_ask_qty",   &OrderBook::total_ask_qty)
        .def("bid_levels",      &OrderBook::bid_levels)
        .def("ask_levels",      &OrderBook::ask_levels)
        .def("empty",           &OrderBook::empty);

    // ── EngineStats ───────────────────────────────────────────────────────────
    py::class_<EngineStats>(m, "EngineStats")
        .def_readonly("orders_accepted",  &EngineStats::orders_accepted)
        .def_readonly("orders_rejected",  &EngineStats::orders_rejected)
        .def_readonly("orders_cancelled", &EngineStats::orders_cancelled)
        .def_readonly("trades_executed",  &EngineStats::trades_executed)
        .def_readonly("total_volume",     &EngineStats::total_volume);

    // ── MatchingEngine ────────────────────────────────────────────────────────
    py::class_<MatchingEngine>(m, "MatchingEngine")
        .def(py::init<>())
        .def("submit",    &MatchingEngine::submit, py::arg("order"),
             "Submit an order through the engine (validates + matches).")
        .def("cancel",    &MatchingEngine::cancel, py::arg("order_id"))
        .def("best_bid",  &MatchingEngine::best_bid)
        .def("best_ask",  &MatchingEngine::best_ask)
        .def("mid_price", &MatchingEngine::mid_price)
        .def_property_readonly("stats", &MatchingEngine::stats)
        .def("on_trade", &MatchingEngine::on_trade, py::arg("callback"),
             "Register a Python callable invoked on every trade.");
}
