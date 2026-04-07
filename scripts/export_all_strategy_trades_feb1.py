from __future__ import annotations

import json
import sqlite3
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


DB_PATH = Path(r"C:\VScode\NinjaTrader.sqlite")
OUTPUT_PATH = Path(r"C:\VScode\Reports\Live\all-strategies-trades-feb1-onward.json")
START_UTC = "2026-02-01T00:00:00Z"
START_NT_TICKS = 638739648000000000
INVALID_DOUBLE_SENTINEL = 1.7976931348623157e308

ORDER_ACTION_NAMES = {
    0: "Buy",
    1: "BuyToCover",
    2: "Sell",
    3: "SellShort",
}

ORDER_TYPE_NAMES = {
    0: "Market",
    1: "Limit",
    2: "StopMarket",
    3: "StopLimit",
    4: "MarketIfTouched",
    5: "LimitIfTouched",
}

ORDER_STATE_NAMES = {
    0: "Initialized",
    1: "Submitted",
    2: "Filled",
    3: "PartFilled",
    4: "Cancelled",
    5: "Rejected",
    6: "Working",
    7: "Accepted",
    8: "TriggerPending",
    9: "ChangePending",
    10: "ChangeSubmitted",
    11: "Unknown",
}

MARKET_POSITION_NAMES = {
    0: "Long",
    1: "Short",
    2: "Flat",
}

APOLLOES_COPIER_ACCOUNTS = {
    "APEX2988270000082",
    "APEX2988270000083",
    "APEX2988270000084",
    "APEX2988270000085",
}


def nt_ticks_to_utc_iso(value: int | None) -> str | None:
    if not value:
        return None
    unix_seconds = (value - 621355968000000000) / 10_000_000
    return datetime.fromtimestamp(unix_seconds, UTC).isoformat().replace("+00:00", "Z")


def round_money(value: float) -> float:
    return round(value, 2)


def round_price(value: float | None) -> float | None:
    if value is None:
        return None
    return round(value, 8)


def weighted_average(total_notional: float, total_qty: int) -> float | None:
    if total_qty <= 0:
        return None
    return round(total_notional / total_qty, 8)


def sanitize_price_extreme(value: float | None) -> float | None:
    if value is None:
        return None
    numeric = float(value)
    if abs(numeric) >= INVALID_DOUBLE_SENTINEL / 2:
        return None
    return numeric


def infer_side(position_after: int | None, order_action_code: int | None) -> str:
    if position_after is not None:
        if int(position_after) > 0:
            return "long"
        if int(position_after) < 0:
            return "short"
    if order_action_code in (0, 1):
        return "long"
    return "short"


def signed_quantity(side: str, quantity: int) -> int:
    return quantity if side == "long" else -quantity


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=str(path.parent),
        prefix=path.stem + ".",
        suffix=".tmp",
        delete=False,
    ) as handle:
        json.dump(payload, handle, indent=2)
        handle.write("\n")
        temp_path = Path(handle.name)
    temp_path.replace(path)


@dataclass
class TradeAccumulator:
    strategy_name: str
    account_name: str
    account_display_name: str
    instrument_name: str
    instrument_display_name: str
    side: str
    point_value: float
    tick_size: float
    trade_sequence: int
    trade_key: str
    opened_at_utc: str | None = None
    opened_at_nt_ticks: int | None = None
    closed_at_utc: str | None = None
    closed_at_nt_ticks: int | None = None
    current_position: int = 0
    entry_quantity: int = 0
    exit_quantity: int = 0
    entry_notional: float = 0.0
    exit_notional: float = 0.0
    total_commission: float = 0.0
    total_fee: float = 0.0
    entry_executions: list[dict[str, Any]] = field(default_factory=list)
    exit_executions: list[dict[str, Any]] = field(default_factory=list)
    entry_execution_ids: list[str] = field(default_factory=list)
    exit_execution_ids: list[str] = field(default_factory=list)
    entry_order_ids: list[str] = field(default_factory=list)
    exit_order_ids: list[str] = field(default_factory=list)
    entry_signals: list[str] = field(default_factory=list)
    exit_signals: list[str] = field(default_factory=list)
    observed_min_price: float | None = None
    observed_max_price: float | None = None

    def append_execution(self, execution: dict[str, Any]) -> None:
        quantity = int(execution["quantity"])
        price = float(execution["price"])
        self.total_commission += float(execution["execution_commission"])
        self.total_fee += float(execution["execution_fee"])

        observed_min = execution["observed_min_price"]
        observed_max = execution["observed_max_price"]
        if observed_min is not None:
            self.observed_min_price = observed_min if self.observed_min_price is None else min(self.observed_min_price, observed_min)
        if observed_max is not None:
            self.observed_max_price = observed_max if self.observed_max_price is None else max(self.observed_max_price, observed_max)

        if execution["is_entry"]:
            if self.opened_at_utc is None:
                self.opened_at_utc = execution["timestamp_utc"]
                self.opened_at_nt_ticks = execution["timestamp_nt_ticks"]
            self.entry_quantity += quantity
            self.entry_notional += price * quantity
            self.current_position += signed_quantity(self.side, quantity)
            self.entry_executions.append(execution)
            self.entry_execution_ids.append(execution["execution_id"])
            if execution["order_id"]:
                self.entry_order_ids.append(execution["order_id"])
            if execution["execution_signal_name"]:
                self.entry_signals.append(execution["execution_signal_name"])
            return

        self.closed_at_utc = execution["timestamp_utc"]
        self.closed_at_nt_ticks = execution["timestamp_nt_ticks"]
        self.exit_quantity += quantity
        self.exit_notional += price * quantity
        self.current_position -= signed_quantity(self.side, quantity)
        self.exit_executions.append(execution)
        self.exit_execution_ids.append(execution["execution_id"])
        if execution["order_id"]:
            self.exit_order_ids.append(execution["order_id"])
        if execution["execution_signal_name"]:
            self.exit_signals.append(execution["execution_signal_name"])

    def is_closed(self) -> bool:
        return self.current_position == 0 and self.entry_quantity > 0 and self.exit_quantity > 0

    def to_payload(self) -> dict[str, Any]:
        entry_avg_price = weighted_average(self.entry_notional, self.entry_quantity)
        exit_avg_price = weighted_average(self.exit_notional, self.exit_quantity)
        if self.side == "long":
            gross_pnl = (self.exit_notional - self.entry_notional) * self.point_value
        else:
            gross_pnl = (self.entry_notional - self.exit_notional) * self.point_value
        total_costs = self.total_commission + self.total_fee
        net_pnl = gross_pnl - total_costs
        price_change = None
        price_change_ticks = None
        if entry_avg_price is not None and exit_avg_price is not None:
            raw_change = exit_avg_price - entry_avg_price
            price_change = raw_change if self.side == "long" else -raw_change
            if self.tick_size > 0:
                price_change_ticks = price_change / self.tick_size
        duration_seconds = None
        if self.opened_at_nt_ticks and self.closed_at_nt_ticks:
            duration_seconds = round((self.closed_at_nt_ticks - self.opened_at_nt_ticks) / 10_000_000, 3)
        return {
            "trade_id": self.trade_key,
            "trade_sequence": self.trade_sequence,
            "strategy_name": self.strategy_name,
            "account_name": self.account_name,
            "account_display_name": self.account_display_name,
            "instrument_name": self.instrument_name,
            "instrument_display_name": self.instrument_display_name,
            "side": self.side,
            "status": "closed" if self.is_closed() else "open",
            "opened_at_utc": self.opened_at_utc,
            "opened_at_nt_ticks": self.opened_at_nt_ticks,
            "closed_at_utc": self.closed_at_utc,
            "closed_at_nt_ticks": self.closed_at_nt_ticks,
            "duration_seconds": duration_seconds,
            "point_value": self.point_value,
            "tick_size": self.tick_size,
            "entry_quantity": self.entry_quantity,
            "exit_quantity": self.exit_quantity,
            "open_quantity": abs(self.current_position),
            "entry_avg_price": round_price(entry_avg_price),
            "exit_avg_price": round_price(exit_avg_price),
            "price_change_points": round_price(price_change),
            "price_change_ticks": round_price(price_change_ticks),
            "gross_pnl": round_money(gross_pnl),
            "commission": round_money(self.total_commission),
            "fee": round_money(self.total_fee),
            "total_costs": round_money(total_costs),
            "net_pnl": round_money(net_pnl),
            "observed_min_price": round_price(self.observed_min_price),
            "observed_max_price": round_price(self.observed_max_price),
            "entry_execution_count": len(self.entry_executions),
            "exit_execution_count": len(self.exit_executions),
            "entry_execution_ids": self.entry_execution_ids,
            "exit_execution_ids": self.exit_execution_ids,
            "entry_order_ids": sorted(set(self.entry_order_ids)),
            "exit_order_ids": sorted(set(self.exit_order_ids)),
            "entry_signals": self.entry_signals,
            "exit_signals": self.exit_signals,
            "entry_executions": self.entry_executions,
            "exit_executions": self.exit_executions,
        }


def infer_strategy_name(
    mapped_strategy: str | None,
    execution_name: str | None,
    order_name: str | None,
    account_name: str,
    instrument_name: str,
) -> str:
    if mapped_strategy:
        return mapped_strategy

    execution_upper = (execution_name or "").strip().upper()
    order_upper = (order_name or "").strip().upper()
    account_upper = (account_name or "").strip().upper()
    instrument_upper = (instrument_name or "").strip().upper()

    if order_upper == "SYNAPSELITE" or execution_upper == "SYNAPSELITE":
        return "ApolloES"
    if account_upper in APOLLOES_COPIER_ACCOUNTS:
        return "ApolloES"
    if execution_upper.startswith("IBL") or execution_upper.startswith("IBH") or order_upper.startswith("IBL") or order_upper.startswith("IBH"):
        return "Hermes"
    if execution_upper.startswith("APOLLO_") or order_upper.startswith("APOLLO_"):
        return "Apollo"
    if account_upper in {"APEX2988270000074", "APEX2988270000075"} and instrument_upper in {"NQ", "MNQ"}:
        return "Hermes"
    return "Unknown"


def resolve_strategy_name_for_row(
    row: sqlite3.Row,
    active_trades: dict[tuple[str, str, str], TradeAccumulator],
) -> str:
    inferred = infer_strategy_name(
        mapped_strategy=row["StrategyName"],
        execution_name=row["ExecutionName"],
        order_name=row["OrderName"],
        account_name=row["AccountName"],
        instrument_name=row["InstrumentName"],
    )
    if inferred != "Unknown":
        return inferred

    account_name = row["AccountName"]
    instrument_name = row["InstrumentName"]
    matching_active = [
        slot_strategy
        for (slot_strategy, slot_account, slot_instrument) in active_trades
        if slot_account == account_name and slot_instrument == instrument_name
    ]
    if bool(row["IsExit"]) and len(matching_active) == 1:
        return matching_active[0]
    return inferred


def fetch_rows(con: sqlite3.Connection) -> list[sqlite3.Row]:
    query = """
        SELECT
            e.Id AS ExecutionRowId,
            e.ExecutionId,
            e.OrderId,
            e.IsEntry,
            e.IsExit,
            e.Position AS PositionAfter,
            e.MarketPosition AS MarketPositionCode,
            e.Quantity,
            e.Price,
            e.Commission,
            e.Fee,
            e.MinPrice AS ObservedMinPrice,
            e.MaxPrice AS ObservedMaxPrice,
            e.Name AS ExecutionName,
            e.Time AS ExecutionTime,
            a.Name AS AccountName,
            a.DisplayName AS AccountDisplayName,
            mi.Name AS InstrumentName,
            mi.Description AS InstrumentDisplayName,
            mi.PointValue,
            mi.TickSize,
            o.Id AS OrderRowId,
            o.Name AS OrderName,
            o.AvgFillPrice AS OrderAverageFillPrice,
            o.Filled AS OrderFilledQuantity,
            o.Quantity AS OrderQuantity,
            o.OrderAction AS OrderActionCode,
            o.OrderType AS OrderTypeCode,
            o.OrderState AS OrderStateCode,
            o.LimitPrice,
            o.StopPrice,
            o.Oco,
            o.Time AS OrderTime,
            s.Name AS StrategyName
        FROM Executions e
        JOIN Accounts a ON a.Id = e.Account
        JOIN Instruments i ON i.Id = e.Instrument
        JOIN MasterInstruments mi ON mi.Id = i.MasterInstrument
        LEFT JOIN Orders o ON o.OrderId = e.OrderId
        LEFT JOIN Strategy2Order so ON so.[Order] = o.Id
        LEFT JOIN Strategies s ON s.Id = so.Strategy
        WHERE e.Time >= ?
        ORDER BY e.Time ASC, e.Id ASC
    """
    return list(con.execute(query, (START_NT_TICKS,)))


def build_execution_payload(row: sqlite3.Row, strategy_name: str) -> dict[str, Any]:
    commission = float(row["Commission"] or 0.0)
    fee = float(row["Fee"] or 0.0)
    return {
        "execution_row_id": int(row["ExecutionRowId"]),
        "execution_id": row["ExecutionId"],
        "strategy_name": strategy_name,
        "account_name": row["AccountName"],
        "account_display_name": row["AccountDisplayName"] or row["AccountName"],
        "instrument_name": row["InstrumentName"],
        "instrument_display_name": row["InstrumentDisplayName"] or row["InstrumentName"],
        "point_value": float(row["PointValue"] or 0.0),
        "tick_size": float(row["TickSize"] or 0.0),
        "timestamp_utc": nt_ticks_to_utc_iso(row["ExecutionTime"]),
        "timestamp_nt_ticks": int(row["ExecutionTime"] or 0),
        "order_timestamp_utc": nt_ticks_to_utc_iso(row["OrderTime"]),
        "order_timestamp_nt_ticks": int(row["OrderTime"] or 0) if row["OrderTime"] else None,
        "quantity": int(row["Quantity"] or 0),
        "price": float(row["Price"] or 0.0),
        "is_entry": bool(row["IsEntry"]),
        "is_exit": bool(row["IsExit"]),
        "position_after_execution": int(row["PositionAfter"] or 0),
        "market_position_after_execution_code": int(row["MarketPositionCode"] or 0),
        "market_position_after_execution": MARKET_POSITION_NAMES.get(int(row["MarketPositionCode"] or 0), "Unknown"),
        "execution_signal_name": row["ExecutionName"] or "",
        "order_id": row["OrderId"] or "",
        "order_row_id": int(row["OrderRowId"]) if row["OrderRowId"] is not None else None,
        "order_name": row["OrderName"] or "",
        "order_action_code": int(row["OrderActionCode"]) if row["OrderActionCode"] is not None else None,
        "order_action": ORDER_ACTION_NAMES.get(row["OrderActionCode"], "Unknown"),
        "order_type_code": int(row["OrderTypeCode"]) if row["OrderTypeCode"] is not None else None,
        "order_type": ORDER_TYPE_NAMES.get(row["OrderTypeCode"], "Unknown"),
        "order_state_code": int(row["OrderStateCode"]) if row["OrderStateCode"] is not None else None,
        "order_state": ORDER_STATE_NAMES.get(row["OrderStateCode"], "Unknown"),
        "order_quantity": int(row["OrderQuantity"] or 0),
        "order_filled_quantity": int(row["OrderFilledQuantity"] or 0),
        "order_average_fill_price": float(row["OrderAverageFillPrice"] or 0.0),
        "limit_price": float(row["LimitPrice"] or 0.0),
        "stop_price": float(row["StopPrice"] or 0.0),
        "oco": row["Oco"] or "",
        "execution_commission": commission,
        "execution_fee": fee,
        "execution_costs_total": round_money(commission + fee),
        "observed_min_price": sanitize_price_extreme(row["ObservedMinPrice"]),
        "observed_max_price": sanitize_price_extreme(row["ObservedMaxPrice"]),
    }


def build_payload() -> dict[str, Any]:
    con = sqlite3.connect(str(DB_PATH))
    con.row_factory = sqlite3.Row
    try:
        rows = fetch_rows(con)
    finally:
        con.close()

    active: dict[tuple[str, str, str], TradeAccumulator] = {}
    trade_sequences: dict[tuple[str, str, str], int] = {}
    closed_trades: list[dict[str, Any]] = []
    execution_count_by_strategy: dict[str, int] = {}

    for row in rows:
        strategy_name = resolve_strategy_name_for_row(row, active)
        execution_count_by_strategy[strategy_name] = execution_count_by_strategy.get(strategy_name, 0) + 1

        slot = (strategy_name, row["AccountName"], row["InstrumentName"])
        trade = active.get(slot)
        if trade is None:
            trade_sequences[slot] = trade_sequences.get(slot, 0) + 1
            trade = TradeAccumulator(
                strategy_name=strategy_name,
                account_name=row["AccountName"],
                account_display_name=row["AccountDisplayName"] or row["AccountName"],
                instrument_name=row["InstrumentName"],
                instrument_display_name=row["InstrumentDisplayName"] or row["InstrumentName"],
                side=infer_side(row["PositionAfter"], row["OrderActionCode"]),
                point_value=float(row["PointValue"] or 0.0),
                tick_size=float(row["TickSize"] or 0.0),
                trade_sequence=trade_sequences[slot],
                trade_key=f"{strategy_name}|{row['AccountName']}|{row['InstrumentName']}|{trade_sequences[slot]:05d}",
            )
            active[slot] = trade

        trade.append_execution(build_execution_payload(row, strategy_name))
        if trade.is_closed():
            closed_trades.append(trade.to_payload())
            active.pop(slot, None)

    open_trades = [trade.to_payload() for trade in active.values()]
    closed_trades.sort(key=lambda trade: (trade["closed_at_nt_ticks"] or 0, trade["opened_at_nt_ticks"] or 0, trade["trade_id"]))

    closed_counts: dict[str, int] = {}
    open_counts: dict[str, int] = {}
    closed_net_pnl: dict[str, float] = {}
    for trade in closed_trades:
        s = trade["strategy_name"]
        closed_counts[s] = closed_counts.get(s, 0) + 1
        closed_net_pnl[s] = closed_net_pnl.get(s, 0.0) + float(trade["net_pnl"] or 0.0)
    for trade in open_trades:
        s = trade["strategy_name"]
        open_counts[s] = open_counts.get(s, 0) + 1

    all_trade_timestamps = []
    for trade in closed_trades:
        if trade["opened_at_utc"]:
            all_trade_timestamps.append(trade["opened_at_utc"])
        if trade["closed_at_utc"]:
            all_trade_timestamps.append(trade["closed_at_utc"])
    for trade in open_trades:
        if trade["opened_at_utc"]:
            all_trade_timestamps.append(trade["opened_at_utc"])

    payload = {
        "generated_at_utc": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "source": {
            "db_path": str(DB_PATH),
            "start_utc_filter": START_UTC,
            "execution_rows_loaded": len(rows),
        },
        "summary": {
            "closed_trade_count": len(closed_trades),
            "open_trade_count": len(open_trades),
            "execution_count_by_strategy": execution_count_by_strategy,
            "closed_trade_count_by_strategy": closed_counts,
            "open_trade_count_by_strategy": open_counts,
            "closed_net_pnl_by_strategy": {k: round_money(v) for k, v in closed_net_pnl.items()},
            "record_start_utc": min(all_trade_timestamps) if all_trade_timestamps else None,
            "record_end_utc": max(all_trade_timestamps) if all_trade_timestamps else None,
        },
        "closed_trades": closed_trades,
        "open_trades": open_trades,
        "notes": [
            "This file is separate from the ApolloES/Hermes-only export and does not modify it.",
            "Rows are included from 2026-02-01T00:00:00Z onward.",
            "Strategy names come from Strategy2Order when present, then execution/order-name heuristics, then active-trade inheritance for untagged exits, else Unknown.",
            "SynapseLite copier-labeled rows are intentionally rolled into ApolloES per current workspace convention.",
        ],
    }
    return payload


def main() -> None:
    payload = build_payload()
    atomic_write_json(OUTPUT_PATH, payload)
    print(f"Wrote {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
