from __future__ import annotations

import argparse
import json
import sqlite3
import tempfile
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
import traceback
from typing import Any
from zoneinfo import ZoneInfo


DEFAULT_WORKSPACE_DB = Path(r"C:\VScode\NinjaTrader.sqlite")
DEFAULT_NT_DB = Path.home() / "Documents" / "NinjaTrader 8" / "db" / "NinjaTrader.sqlite"
DEFAULT_OUTPUT = Path(r"C:\VScode\Reports\Live\apolloes-hermes-live-trades.json")
DEFAULT_STRATEGIES = ("ApolloES", "ApolloMES", "Hermes", "Lazarus", "Icarus")
SQLITE_TIMEOUT_SECONDS = 5.0
INVALID_DOUBLE_SENTINEL = 1.7976931348623157e308
ATOMIC_REPLACE_MAX_ATTEMPTS = 10
ATOMIC_REPLACE_RETRY_SECONDS = 0.25
try:
    SESSION_TIMEZONE = ZoneInfo("America/New_York")
except Exception:
    SESSION_TIMEZONE = None
DB_CONFIRMATION_WINDOW_SECONDS = 60.0
ORDER_LIFECYCLE_STALE_SECONDS = 300.0
RUNTIME_STATUS_STALE_SECONDS = 300.0
RUNTIME_STATUS_FUTURE_SKEW_SECONDS = 120.0
ORDER_ACCEPTED_STATE_CODES = {1, 2, 3, 6, 7, 8, 9, 10}

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


def choose_default_db_path() -> Path:
    return DEFAULT_WORKSPACE_DB if DEFAULT_WORKSPACE_DB.exists() else DEFAULT_NT_DB


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Export live NinjaTrader ApolloES/ApolloMES/Hermes/Lazarus/Icarus trades into a JSON file and optionally keep it updated in real time."
        )
    )
    parser.add_argument(
        "--db-path",
        default=str(choose_default_db_path()),
        help="Path to NinjaTrader.sqlite.",
    )
    parser.add_argument(
        "--output",
        default=str(DEFAULT_OUTPUT),
        help="Path to the JSON file to write.",
    )
    parser.add_argument(
        "--strategy",
        action="append",
        default=[],
        help="Strategy name to include. Repeat for multiple values. Defaults to ApolloES, ApolloMES, Hermes, Lazarus, and Icarus.",
    )
    parser.add_argument(
        "--max-closed-trades",
        type=int,
        default=200,
        help="Maximum number of most-recent closed trades to keep in the JSON. Use 0 for all.",
    )
    parser.add_argument(
        "--poll-seconds",
        type=float,
        default=2.0,
        help="Polling interval used with --watch.",
    )
    parser.add_argument(
        "--watch",
        action="store_true",
        help="Keep running and rewrite the JSON whenever new executions arrive.",
    )
    parser.add_argument(
        "--log-path",
        default="",
        help="Optional log file path for exporter lifecycle and exception messages.",
    )
    parser.add_argument(
        "--runtime-status-dir",
        default="",
        help="Optional directory containing per-strategy runtime-status JSON files to merge into the output.",
    )
    return parser.parse_args()


def nt_ticks_to_utc_iso(value: int | None) -> str | None:
    if not value:
        return None
    return nt_ticks_to_datetime_utc(value).isoformat().replace("+00:00", "Z")


def nt_ticks_to_datetime_utc(value: int | None) -> datetime | None:
    if not value:
        return None
    unix_seconds = (value - 621355968000000000) / 10_000_000
    return datetime.fromtimestamp(unix_seconds, UTC)


def sanitize_price_extreme(value: float | None) -> float | None:
    if value is None:
        return None
    numeric = float(value)
    if abs(numeric) >= INVALID_DOUBLE_SENTINEL / 2:
        return None
    return numeric


def weighted_average(total_notional: float, total_qty: int) -> float | None:
    if total_qty <= 0:
        return None
    return round(total_notional / total_qty, 8)


def round_money(value: float) -> float:
    return round(value, 2)


def round_price(value: float | None) -> float | None:
    if value is None:
        return None
    return round(value, 8)


def normalize_strategy_name(
    mapped_strategy: str | None,
    execution_name: str | None,
    order_name: str | None,
) -> str | None:
    if mapped_strategy:
        return mapped_strategy

    name_candidates = [execution_name or "", order_name or ""]
    for candidate in name_candidates:
        if candidate.startswith("IBL") or candidate.startswith("IBH"):
            return "Hermes"
        if candidate.startswith("APOLLO_"):
            return "ApolloES"
        if candidate.startswith("ICARUS_"):
            return "Icarus"
    return None


def parse_iso_utc(value: str | None) -> datetime | None:
    if not value:
        return None
    normalized = value.strip()
    if not normalized:
        return None
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def session_date_from_utc(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if SESSION_TIMEZONE is not None:
        return dt.astimezone(SESSION_TIMEZONE).date().isoformat()
    return utc_to_us_eastern(dt).date().isoformat()


def utc_to_us_eastern(dt: datetime) -> datetime:
    utc_dt = dt.astimezone(UTC)
    year = utc_dt.year

    dst_start_local = nth_weekday_of_month(year, 3, 6, 2, 2, 0)
    dst_end_local = nth_weekday_of_month(year, 11, 6, 1, 2, 0)
    dst_start_utc = (dst_start_local + timedelta(hours=5)).replace(tzinfo=UTC)
    dst_end_utc = (dst_end_local + timedelta(hours=4)).replace(tzinfo=UTC)

    offset_hours = -4 if dst_start_utc <= utc_dt < dst_end_utc else -5
    eastern = timezone(timedelta(hours=offset_hours))
    return utc_dt.astimezone(eastern)


def nth_weekday_of_month(
    year: int,
    month: int,
    weekday: int,
    occurrence: int,
    hour: int,
    minute: int,
) -> datetime:
    current = datetime(year, month, 1, hour, minute)
    days_until_weekday = (weekday - current.weekday()) % 7
    day = 1 + days_until_weekday + ((occurrence - 1) * 7)
    return datetime(year, month, day, hour, minute)


def resolve_strategy_name_for_row(
    row: sqlite3.Row,
    target_strategies: list[str],
    active_trades: dict[tuple[str, str, str], TradeAccumulator],
) -> str | None:
    strategy_name = normalize_strategy_name(
        mapped_strategy=row["StrategyName"],
        execution_name=row["ExecutionName"],
        order_name=row["OrderName"],
    )
    if strategy_name in target_strategies:
        return strategy_name

    account_name = row["AccountName"]
    instrument_name = row["InstrumentName"]
    matching_active_strategies = [
        slot_strategy
        for (slot_strategy, slot_account, slot_instrument) in active_trades
        if slot_account == account_name and slot_instrument == instrument_name
    ]

    if bool(row["IsExit"]) and len(matching_active_strategies) == 1:
        return matching_active_strategies[0]

    return strategy_name


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

    try:
        last_error: Exception | None = None
        for attempt in range(1, ATOMIC_REPLACE_MAX_ATTEMPTS + 1):
            try:
                temp_path.replace(path)
                last_error = None
                break
            except PermissionError as exc:
                last_error = exc
                if attempt >= ATOMIC_REPLACE_MAX_ATTEMPTS:
                    raise
                time.sleep(ATOMIC_REPLACE_RETRY_SECONDS)
        if last_error is not None:
            raise last_error
    except Exception:
        if temp_path.exists():
            temp_path.unlink(missing_ok=True)
        raise


def choose_private_output_path(output_path: Path) -> Path:
    return output_path.with_name(output_path.stem + ".writer.json")


def publish_public_snapshot(public_path: Path, payload: dict[str, Any], log_path: Path | None = None) -> bool:
    try:
        atomic_write_json(public_path, payload)
        return True
    except Exception as exc:
        append_log_line(log_path, f"Public snapshot publish failed for {public_path}: {exc}")
        return False


def choose_runtime_status_dir(output_path: Path) -> Path:
    if output_path.parent.name.lower() == "out":
        return output_path.parent.parent / "runtime-status"
    return output_path.parent / "runtime-status"


def append_log_line(log_path: Path | None, message: str) -> None:
    if log_path is None:
        return

    log_path.parent.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(f"[{timestamp}] {message}\n")


def isoformat_utc(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")


def file_mtime_utc(path: Path) -> datetime | None:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, UTC)
    except Exception:
        return None


def build_execution_payload(row: sqlite3.Row, strategy_name: str) -> dict[str, Any]:
    commission = float(row["Commission"] or 0.0)
    fee = float(row["Fee"] or 0.0)
    quantity = int(row["Quantity"] or 0)
    payload = {
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
        "quantity": quantity,
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
    return payload


def load_runtime_statuses(
    runtime_status_dir: Path,
    target_strategies: list[str],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    summary: dict[str, Any] = {
        "directory_exists": runtime_status_dir.exists(),
        "directory_path": str(runtime_status_dir),
        "files_seen": 0,
        "files_loaded": 0,
        "non_object_count": 0,
        "parse_error_count": 0,
        "non_target_strategy_count": 0,
        "missing_identity_count": 0,
        "missing_heartbeat_count": 0,
        "future_heartbeat_count": 0,
        "stale_heartbeat_count": 0,
        "latest_seen_heartbeat_utc": None,
        "latest_loaded_heartbeat_utc": None,
        "latest_file_mtime_utc": None,
    }
    if not runtime_status_dir.exists():
        return [], summary

    payloads: list[dict[str, Any]] = []
    now_utc = datetime.now(UTC)
    for path in sorted(runtime_status_dir.glob("*.json")):
        summary["files_seen"] = int(summary["files_seen"]) + 1
        path_mtime_utc = file_mtime_utc(path)
        latest_file_mtime_utc = parse_iso_utc(summary["latest_file_mtime_utc"])
        if path_mtime_utc is not None and (
            latest_file_mtime_utc is None or path_mtime_utc > latest_file_mtime_utc
        ):
            summary["latest_file_mtime_utc"] = isoformat_utc(path_mtime_utc)
        try:
            parsed = json.loads(path.read_text(encoding="utf-8-sig"))
        except Exception:
            summary["parse_error_count"] = int(summary["parse_error_count"]) + 1
            continue

        if not isinstance(parsed, dict):
            summary["non_object_count"] = int(summary["non_object_count"]) + 1
            continue

        strategy_name = parsed.get("strategy")
        if strategy_name not in target_strategies:
            summary["non_target_strategy_count"] = int(summary["non_target_strategy_count"]) + 1
            continue

        if not parsed.get("account") or not parsed.get("instrument"):
            summary["missing_identity_count"] = int(summary["missing_identity_count"]) + 1
            continue

        heartbeat_utc = parse_iso_utc(parsed.get("last_heartbeat_utc"))
        if heartbeat_utc is None:
            summary["missing_heartbeat_count"] = int(summary["missing_heartbeat_count"]) + 1
            continue

        latest_seen_heartbeat_utc = parse_iso_utc(summary["latest_seen_heartbeat_utc"])
        if latest_seen_heartbeat_utc is None or heartbeat_utc > latest_seen_heartbeat_utc:
            summary["latest_seen_heartbeat_utc"] = isoformat_utc(heartbeat_utc)

        heartbeat_age_seconds = (now_utc - heartbeat_utc).total_seconds()
        if heartbeat_age_seconds < (-1 * RUNTIME_STATUS_FUTURE_SKEW_SECONDS):
            summary["future_heartbeat_count"] = int(summary["future_heartbeat_count"]) + 1
            continue
        if heartbeat_age_seconds > RUNTIME_STATUS_STALE_SECONDS:
            summary["stale_heartbeat_count"] = int(summary["stale_heartbeat_count"]) + 1
            continue

        parsed["_runtime_heartbeat_age_seconds"] = round(max(heartbeat_age_seconds, 0.0), 3)
        parsed["_runtime_source_file"] = str(path)
        payloads.append(parsed)
        summary["files_loaded"] = int(summary["files_loaded"]) + 1

        latest_loaded_heartbeat_utc = parse_iso_utc(summary["latest_loaded_heartbeat_utc"])
        if latest_loaded_heartbeat_utc is None or heartbeat_utc > latest_loaded_heartbeat_utc:
            summary["latest_loaded_heartbeat_utc"] = isoformat_utc(heartbeat_utc)

    payloads.sort(
        key=lambda item: (
            str(item.get("strategy", "")),
            str(item.get("account", "")),
            str(item.get("instance_key", "")),
        )
    )
    return payloads, summary


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
    entry_execution_ids: list[str] = field(default_factory=list)
    exit_execution_ids: list[str] = field(default_factory=list)
    entry_order_ids: list[str] = field(default_factory=list)
    exit_order_ids: list[str] = field(default_factory=list)
    entry_signals: list[str] = field(default_factory=list)
    exit_signals: list[str] = field(default_factory=list)
    entry_executions: list[dict[str, Any]] = field(default_factory=list)
    exit_executions: list[dict[str, Any]] = field(default_factory=list)
    observed_min_price: float | None = None
    observed_max_price: float | None = None

    def append_execution(self, execution: dict[str, Any]) -> None:
        quantity = int(execution["quantity"])
        price = float(execution["price"])
        cost_total = float(execution["execution_costs_total"])
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


def fetch_execution_rows(con: sqlite3.Connection, target_strategies: list[str]) -> list[sqlite3.Row]:
    placeholders = ",".join("?" for _ in target_strategies)
    query = f"""
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
        WHERE a.Id IN (
            SELECT sa.Account
            FROM Strategy2Account sa
            JOIN Strategies st ON st.Id = sa.Strategy
            WHERE st.Name IN ({placeholders})
        )
        ORDER BY e.Time ASC, e.Id ASC
    """
    return list(con.execute(query, tuple(target_strategies)))


def fetch_order_rows(con: sqlite3.Connection, target_strategies: list[str]) -> list[sqlite3.Row]:
    placeholders = ",".join("?" for _ in target_strategies)
    query = f"""
        SELECT
            o.Id AS OrderRowId,
            o.OrderId,
            o.Name AS OrderName,
            o.Time AS OrderTime,
            o.OrderState AS OrderStateCode,
            o.OrderAction AS OrderActionCode,
            o.OrderType AS OrderTypeCode,
            o.Quantity AS OrderQuantity,
            o.Filled AS OrderFilledQuantity,
            o.AvgFillPrice AS OrderAverageFillPrice,
            o.LimitPrice,
            o.StopPrice,
            o.Oco,
            a.Name AS AccountName,
            a.DisplayName AS AccountDisplayName,
            mi.Name AS InstrumentName,
            mi.Description AS InstrumentDisplayName,
            s.Name AS StrategyName
        FROM Orders o
        JOIN Accounts a ON a.Id = o.Account
        JOIN Instruments i ON i.Id = o.Instrument
        JOIN MasterInstruments mi ON mi.Id = i.MasterInstrument
        LEFT JOIN Strategy2Order so ON so.[Order] = o.Id
        LEFT JOIN Strategies s ON s.Id = so.Strategy
        WHERE a.Id IN (
            SELECT sa.Account
            FROM Strategy2Account sa
            JOIN Strategies st ON st.Id = sa.Strategy
            WHERE st.Name IN ({placeholders})
        )
        ORDER BY o.Time ASC, o.Id ASC
    """
    return list(con.execute(query, tuple(target_strategies)))


def fetch_all_execution_rows(con: sqlite3.Connection) -> list[sqlite3.Row]:
    query = """
        SELECT
            e.Id AS ExecutionRowId,
            e.ExecutionId,
            e.OrderId,
            e.IsEntry,
            e.IsExit,
            e.Position AS PositionAfter,
            e.PositionStrategy AS PositionStrategyId,
            e.MarketPosition AS MarketPositionCode,
            e.Quantity,
            e.Price,
            e.Commission,
            e.Fee,
            e.Time AS ExecutionTime,
            a.Name AS AccountName,
            mi.Name AS InstrumentName,
            mi.PointValue,
            mi.TickSize,
            o.OrderAction AS OrderActionCode
        FROM Executions e
        JOIN Accounts a ON a.Id = e.Account
        JOIN Instruments i ON i.Id = e.Instrument
        JOIN MasterInstruments mi ON mi.Id = i.MasterInstrument
        LEFT JOIN Orders o ON o.OrderId = e.OrderId
        ORDER BY e.Time ASC, e.Id ASC
    """
    return list(con.execute(query))


@dataclass
class SessionRealizedAccumulator:
    side: str
    point_value: float
    current_position: int = 0
    entry_quantity: int = 0
    exit_quantity: int = 0
    entry_notional: float = 0.0
    exit_notional: float = 0.0
    total_commission: float = 0.0
    total_fee: float = 0.0

    def append_execution(self, row: sqlite3.Row) -> None:
        quantity = int(row["Quantity"] or 0)
        price = float(row["Price"] or 0.0)
        self.total_commission += float(row["Commission"] or 0.0)
        self.total_fee += float(row["Fee"] or 0.0)

        if bool(row["IsEntry"]):
            self.entry_quantity += quantity
            self.entry_notional += price * quantity
            self.current_position += signed_quantity(self.side, quantity)
            return

        self.exit_quantity += quantity
        self.exit_notional += price * quantity
        self.current_position -= signed_quantity(self.side, quantity)

    def is_closed(self) -> bool:
        return self.current_position == 0 and self.entry_quantity > 0 and self.exit_quantity > 0

    def realized_gross(self) -> float:
        if self.side == "long":
            return (self.exit_notional - self.entry_notional) * self.point_value
        return (self.entry_notional - self.exit_notional) * self.point_value

    def realized_net(self) -> float:
        return self.realized_gross() - self.total_commission - self.total_fee


def compute_execution_derived_session_realized(all_execution_rows: list[sqlite3.Row]) -> dict[str, Any]:
    now_session_date = session_date_from_utc(datetime.now(UTC))
    if now_session_date is None:
        return {
            "session_date": None,
            "closed_trade_count": 0,
            "realized_gross_pnl": 0.0,
            "realized_total_costs": 0.0,
            "realized_net_pnl": 0.0,
            "realized_net_pnl_by_account": {},
        }

    active: dict[tuple[str, str, int], SessionRealizedAccumulator] = {}
    closed_trade_count = 0
    realized_gross_total = 0.0
    realized_costs_total = 0.0
    realized_net_by_account: dict[str, float] = {}

    for row in all_execution_rows:
        execution_dt = nt_ticks_to_datetime_utc(row["ExecutionTime"])
        execution_session_date = session_date_from_utc(execution_dt)
        if execution_session_date is None:
            continue

        account_name = str(row["AccountName"] or "")
        instrument_name = str(row["InstrumentName"] or "")
        position_strategy_id = int(row["PositionStrategyId"] or 0)
        slot_key = (account_name, instrument_name, position_strategy_id)

        accumulator = active.get(slot_key)
        if accumulator is None:
            side = infer_side(row["PositionAfter"], row["OrderActionCode"])
            accumulator = SessionRealizedAccumulator(
                side=side,
                point_value=float(row["PointValue"] or 0.0),
            )
            active[slot_key] = accumulator

        accumulator.append_execution(row)

        if accumulator.is_closed():
            gross_pnl = accumulator.realized_gross()
            net_pnl = accumulator.realized_net()
            costs = accumulator.total_commission + accumulator.total_fee

            # Attribute realized PnL to the session date on which the trade closes.
            if execution_session_date == now_session_date:
                realized_gross_total += gross_pnl
                realized_costs_total += costs
                realized_net_by_account[account_name] = realized_net_by_account.get(account_name, 0.0) + net_pnl
                closed_trade_count += 1
            active.pop(slot_key, None)

    return {
        "session_date": now_session_date,
        "closed_trade_count": closed_trade_count,
        "realized_gross_pnl": round_money(realized_gross_total),
        "realized_total_costs": round_money(realized_costs_total),
        "realized_net_pnl": round_money(realized_gross_total - realized_costs_total),
        "realized_net_pnl_by_account": {
            account: round_money(value) for account, value in sorted(realized_net_by_account.items())
        },
    }


def normalize_runtime_status_code(raw_status_code: str | None, runtime_status: dict[str, Any]) -> str:
    code = str(raw_status_code or "").strip().upper()
    if code in {"ALIVE"}:
        return "ALIVE"
    if code in {"PROCESS_SEEN", "CONFIG_READY"}:
        return "ALIVE"
    if code in {"DATA_SERIES_READY", "REALTIME_READY"}:
        return "REALTIME_READY"
    if code in {"SESSION_READY", "ORB_WINDOW_ACTIVE", "WAITING_FOR_ORB"}:
        return "WAITING_FOR_ORB"
    if code in {"ORB_FORMED"}:
        return "ORB_FORMED"
    if code in {"ORB_SKIP", "ORB_SKIPPED"}:
        return "ORB_SKIPPED"
    if code in {"ENTRY_SUBMITTED", "ORDER_SENT", "LIVE_ORDER_CONFIRMED", "LIVE_EXECUTION_CONFIRMED"}:
        return "ORDER_SENT"
    if code in {"ERROR"}:
        return "ERROR"
    if code in {"DESYNC_SUSPECTED"}:
        return "DESYNC_SUSPECTED"
    return "ALIVE"


def status_rank(status_code: str) -> int:
    ladder = {
        "ALIVE": 1,
        "REALTIME_READY": 2,
        "WAITING_FOR_ORB": 3,
        "ORB_FORMED": 4,
        "ORB_SKIPPED": 4,
        "ORDER_SENT": 5,
        "BROKER_ACCEPTED": 6,
        "EXECUTION_CONFIRMED": 7,
        "ERROR": 8,
        "DESYNC_SUSPECTED": 9,
        "BROKER_ACCEPTANCE_ISSUE": 10,
    }
    return ladder.get(status_code, 0)


def message_for_status(
    status_code: str,
    orb_skip_reason: str | None = None,
    runtime_status_message: str | None = None,
    last_error: str | None = None,
) -> str:
    if status_code == "ALIVE":
        return "I am alive."
    if status_code == "REALTIME_READY":
        return "Realtime and all required data series are loaded."
    if status_code == "WAITING_FOR_ORB":
        return "I am waiting for today's ORB."
    if status_code == "ORB_FORMED":
        return "The ORB formed and passed validation."
    if status_code == "ORB_SKIPPED":
        return f"Today's ORB was skipped: {orb_skip_reason}." if orb_skip_reason else "Today's ORB was skipped."
    if status_code == "ORDER_SENT":
        return "I sent the order to the broker."
    if status_code == "BROKER_ACCEPTED":
        return "The broker accepted the order."
    if status_code == "EXECUTION_CONFIRMED":
        return "The execution was confirmed in the NinjaTrader database."
    if status_code == "ERROR":
        if runtime_status_message:
            return runtime_status_message
        if last_error:
            return last_error
        return "Strategy paused managed order automation for safety."
    if status_code == "DESYNC_SUSPECTED":
        return "I suspect a desync between the strategy and persisted broker state."
    if status_code == "BROKER_ACCEPTANCE_ISSUE":
        return "The broker did not accept the order."
    return "I am alive."


def collapse_inactive_runtime_status(base_status: str, runtime_status: dict[str, Any]) -> str:
    if base_status == "ORB_SKIPPED":
        return "ORB_SKIPPED"
    if runtime_status.get("orb_high") is not None and runtime_status.get("orb_low") is not None:
        return "ORB_FORMED"
    if parse_iso_utc(runtime_status.get("session_ready_utc")) is not None:
        return "WAITING_FOR_ORB"
    if parse_iso_utc(runtime_status.get("realtime_entered_utc")) is not None:
        return "REALTIME_READY"
    return "ALIVE"


def normalize_skip_reason(raw_reason: str | None) -> str | None:
    text = (raw_reason or "").strip()
    if not text:
        return None
    if text.startswith("ORB_SKIPPED_RANGE_"):
        return "range too small"
    if text.startswith("ORB_SKIPPED_BODY_"):
        return "body percentage was too small"
    return text.replace("_", " ").lower()


def build_db_confirmation_indexes(
    execution_rows: list[sqlite3.Row],
    order_rows: list[sqlite3.Row],
    target_strategies: list[str],
) -> tuple[dict[tuple[str, str, str, str], list[datetime]], dict[tuple[str, str, str, str], list[datetime]]]:
    execution_index: dict[tuple[str, str, str, str], list[datetime]] = {}
    order_index: dict[tuple[str, str, str, str], list[datetime]] = {}

    for row in execution_rows:
        strategy_name = normalize_strategy_name(
            mapped_strategy=row["StrategyName"],
            execution_name=row["ExecutionName"],
            order_name=row["OrderName"],
        )
        if strategy_name not in target_strategies:
            continue

        execution_dt = nt_ticks_to_datetime_utc(row["ExecutionTime"])
        session_date = session_date_from_utc(execution_dt)
        if execution_dt is None or session_date is None:
            continue

        key = (strategy_name, row["AccountName"], row["InstrumentName"], session_date)
        execution_index.setdefault(key, []).append(execution_dt)

    for row in order_rows:
        strategy_name = normalize_strategy_name(
            mapped_strategy=row["StrategyName"],
            execution_name=None,
            order_name=row["OrderName"],
        )
        if strategy_name not in target_strategies:
            continue

        order_state_code = int(row["OrderStateCode"]) if row["OrderStateCode"] is not None else None
        if order_state_code not in ORDER_ACCEPTED_STATE_CODES:
            continue

        order_dt = nt_ticks_to_datetime_utc(row["OrderTime"])
        session_date = session_date_from_utc(order_dt)
        if order_dt is None or session_date is None:
            continue

        key = (strategy_name, row["AccountName"], row["InstrumentName"], session_date)
        order_index.setdefault(key, []).append(order_dt)

    return execution_index, order_index


def choose_confirmation_time(candidates: list[datetime], anchor_utc: datetime | None) -> datetime | None:
    if not candidates:
        return None
    ordered = sorted(candidates)
    if anchor_utc is None:
        return ordered[0]

    floor = anchor_utc.timestamp() - 5.0
    for candidate in ordered:
        if candidate.timestamp() >= floor:
            return candidate
    return ordered[-1]


def build_hybrid_runtime_statuses(
    runtime_statuses: list[dict[str, Any]],
    execution_rows: list[sqlite3.Row],
    order_rows: list[sqlite3.Row],
    target_strategies: list[str],
    open_trade_keys: set[tuple[str, str, str]],
) -> list[dict[str, Any]]:
    execution_index, order_index = build_db_confirmation_indexes(execution_rows, order_rows, target_strategies)
    hybrid_statuses: list[dict[str, Any]] = []

    for runtime_status in runtime_statuses:
        strategy = str(runtime_status.get("strategy") or "")
        account = str(runtime_status.get("account") or "")
        instrument = str(runtime_status.get("instrument") or "")
        session_date = str(runtime_status.get("session_date") or "")
        key = (strategy, account, instrument, session_date)

        orb_skip_reason = normalize_skip_reason(runtime_status.get("orb_skip_reason"))
        base_status = normalize_runtime_status_code(runtime_status.get("status_code"), runtime_status)
        order_sent_utc = parse_iso_utc(runtime_status.get("order_sent_utc") or runtime_status.get("entry_submitted_utc"))
        realtime_entered_utc = parse_iso_utc(runtime_status.get("realtime_entered_utc"))
        nt_order_callback_seen_utc = parse_iso_utc(
            runtime_status.get("nt_order_callback_seen_utc") or runtime_status.get("live_order_confirmed_utc")
        )
        nt_execution_callback_seen_utc = parse_iso_utc(
            runtime_status.get("nt_execution_callback_seen_utc") or runtime_status.get("live_execution_confirmed_utc")
        )
        last_heartbeat_utc = runtime_status.get("last_heartbeat_utc") or ""
        runtime_status_message = str(runtime_status.get("status_message") or "").strip()
        runtime_order_submission_blocked = bool(
            runtime_status.get("runtime_order_submission_blocked")
            or runtime_status.get("runtimeOrderSubmissionBlocked")
        )
        series_sync = runtime_status.get("series_sync") if isinstance(runtime_status.get("series_sync"), dict) else None
        series_sync_is_synchronized = None
        if isinstance(series_sync, dict):
            raw_is_synchronized = series_sync.get("is_synchronized")
            if isinstance(raw_is_synchronized, bool):
                series_sync_is_synchronized = raw_is_synchronized

        broker_accepted_dt = choose_confirmation_time(order_index.get(key, []), order_sent_utc)
        execution_confirmed_dt = choose_confirmation_time(execution_index.get(key, []), order_sent_utc)

        last_error = str(runtime_status.get("last_error") or "")
        data_sync_error = series_sync_is_synchronized is False
        runtime_safety_error = base_status == "ERROR" and not data_sync_error
        latest_reference_utc = parse_iso_utc(last_heartbeat_utc) or datetime.now(UTC)
        lifecycle_anchor = max(
            [dt for dt in (order_sent_utc, nt_order_callback_seen_utc, nt_execution_callback_seen_utc) if dt is not None],
            default=None,
        )
        lifecycle_is_recent = (
            lifecycle_anchor is not None
            and (latest_reference_utc - lifecycle_anchor).total_seconds() <= ORDER_LIFECYCLE_STALE_SECONDS
        )
        has_open_trade = (strategy, account, instrument) in open_trade_keys
        historical_startup_submission = (
            order_sent_utc is not None
            and realtime_entered_utc is not None
            and order_sent_utc < realtime_entered_utc
        )
        active_order_lifecycle = (
            base_status in {"ORDER_SENT", "DESYNC_SUSPECTED"}
            and not historical_startup_submission
            and not runtime_order_submission_blocked
            and (lifecycle_is_recent or has_open_trade)
        )

        broker_acceptance_issue = False
        if (
            active_order_lifecycle
            and order_sent_utc is not None
            and execution_confirmed_dt is None
            and broker_accepted_dt is None
        ):
            if "Order rejected" in last_error:
                broker_acceptance_issue = True
            elif (datetime.now(UTC) - order_sent_utc).total_seconds() >= DB_CONFIRMATION_WINDOW_SECONDS:
                broker_acceptance_issue = True

        desync_suspected = False
        if (
            active_order_lifecycle
            and nt_execution_callback_seen_utc is not None
            and execution_confirmed_dt is None
            and (datetime.now(UTC) - nt_execution_callback_seen_utc).total_seconds() >= DB_CONFIRMATION_WINDOW_SECONDS
        ):
            desync_suspected = True

        final_status = base_status
        if (
            base_status in {"ORDER_SENT", "DESYNC_SUSPECTED"}
            and not active_order_lifecycle
            and not data_sync_error
            and not runtime_safety_error
        ):
            final_status = collapse_inactive_runtime_status(base_status, runtime_status)
        if broker_accepted_dt is not None and status_rank("BROKER_ACCEPTED") > status_rank(final_status):
            final_status = "BROKER_ACCEPTED"
        if execution_confirmed_dt is not None and status_rank("EXECUTION_CONFIRMED") > status_rank(final_status):
            final_status = "EXECUTION_CONFIRMED"
        if (data_sync_error or runtime_safety_error) and status_rank("ERROR") > status_rank(final_status):
            final_status = "ERROR"
        if desync_suspected and status_rank("DESYNC_SUSPECTED") > status_rank(final_status):
            final_status = "DESYNC_SUSPECTED"
        if broker_acceptance_issue and status_rank("BROKER_ACCEPTANCE_ISSUE") > status_rank(final_status):
            final_status = "BROKER_ACCEPTANCE_ISSUE"

        hybrid_statuses.append(
            {
                "strategy": strategy,
                "account": account,
                "instrument": instrument,
                "instance_key": runtime_status.get("instance_key") or "",
                "session_date": session_date,
                "status_code": final_status,
                "status_message": message_for_status(
                    final_status,
                    orb_skip_reason,
                    runtime_status_message=runtime_status_message or None,
                    last_error=last_error or None,
                ),
                "last_heartbeat_utc": last_heartbeat_utc,
                "is_healthy": (
                    not data_sync_error
                    and not runtime_safety_error
                    and not desync_suspected
                    and not broker_acceptance_issue
                ),
                "last_error": last_error,
                "desync_suspected": desync_suspected,
                "broker_acceptance_issue": broker_acceptance_issue,
                "runtime_order_submission_blocked": runtime_order_submission_blocked,
                "historical_startup_submission": historical_startup_submission,
                "series_sync": series_sync if isinstance(series_sync, dict) else None,
                "orb_skip_reason": orb_skip_reason or "",
                "orb_high": runtime_status.get("orb_high"),
                "orb_low": runtime_status.get("orb_low"),
                "orb_range_ticks": runtime_status.get("orb_range_ticks"),
                "order_sent_utc": order_sent_utc.isoformat().replace("+00:00", "Z") if order_sent_utc else "",
                "realtime_entered_utc": (
                    realtime_entered_utc.isoformat().replace("+00:00", "Z")
                    if realtime_entered_utc
                    else ""
                ),
                "broker_accepted_utc": broker_accepted_dt.isoformat().replace("+00:00", "Z") if broker_accepted_dt else "",
                "execution_confirmed_utc": execution_confirmed_dt.isoformat().replace("+00:00", "Z") if execution_confirmed_dt else "",
                "nt_order_callback_seen_utc": nt_order_callback_seen_utc.isoformat().replace("+00:00", "Z") if nt_order_callback_seen_utc else "",
                "nt_execution_callback_seen_utc": nt_execution_callback_seen_utc.isoformat().replace("+00:00", "Z") if nt_execution_callback_seen_utc else "",
                "heartbeat_age_seconds": runtime_status.get("_runtime_heartbeat_age_seconds"),
                "source_file": runtime_status.get("_runtime_source_file") or "",
            }
        )

    hybrid_statuses.sort(key=lambda item: (str(item["strategy"]), str(item["account"])))
    return hybrid_statuses


def build_payload(
    db_path: Path,
    target_strategies: list[str],
    max_closed_trades: int,
    runtime_status_dir: Path | None = None,
) -> dict[str, Any]:
    con = sqlite3.connect(str(db_path), timeout=SQLITE_TIMEOUT_SECONDS)
    con.row_factory = sqlite3.Row
    try:
        rows = fetch_execution_rows(con, target_strategies)
        order_rows = fetch_order_rows(con, target_strategies)
        all_execution_rows = fetch_all_execution_rows(con)
    finally:
        con.close()

    active: dict[tuple[str, str, str], TradeAccumulator] = {}
    trade_sequences: dict[tuple[str, str, str], int] = {}
    closed_trades: list[dict[str, Any]] = []
    execution_count_by_strategy = {strategy: 0 for strategy in target_strategies}

    for row in rows:
        strategy_name = resolve_strategy_name_for_row(
            row=row,
            target_strategies=target_strategies,
            active_trades=active,
        )
        if strategy_name not in target_strategies:
            continue

        execution_count_by_strategy[strategy_name] = execution_count_by_strategy.get(strategy_name, 0) + 1
        account_name = row["AccountName"]
        instrument_name = row["InstrumentName"]
        trade_slot = (strategy_name, account_name, instrument_name)
        trade = active.get(trade_slot)

        if trade is None:
            trade_sequences[trade_slot] = trade_sequences.get(trade_slot, 0) + 1
            side = infer_side(row["PositionAfter"], row["OrderActionCode"])
            trade_id = (
                f"{strategy_name}|{account_name}|{instrument_name}|{trade_sequences[trade_slot]:05d}"
            )
            trade = TradeAccumulator(
                strategy_name=strategy_name,
                account_name=account_name,
                account_display_name=row["AccountDisplayName"] or account_name,
                instrument_name=instrument_name,
                instrument_display_name=row["InstrumentDisplayName"] or instrument_name,
                side=side,
                point_value=float(row["PointValue"] or 0.0),
                tick_size=float(row["TickSize"] or 0.0),
                trade_sequence=trade_sequences[trade_slot],
                trade_key=trade_id,
            )
            active[trade_slot] = trade

        execution_payload = build_execution_payload(row, strategy_name)
        trade.append_execution(execution_payload)

        if trade.is_closed():
            closed_trades.append(trade.to_payload())
            active.pop(trade_slot, None)

    open_trades = [trade.to_payload() for trade in active.values()]
    closed_trades.sort(
        key=lambda trade: (
            trade["closed_at_nt_ticks"] or 0,
            trade["opened_at_nt_ticks"] or 0,
            trade["trade_id"],
        )
    )
    if max_closed_trades > 0:
        closed_trades = closed_trades[-max_closed_trades:]

    closed_counts = {strategy: 0 for strategy in target_strategies}
    open_counts = {strategy: 0 for strategy in target_strategies}
    closed_net_pnl = {strategy: 0.0 for strategy in target_strategies}

    for trade in closed_trades:
        strategy_name = trade["strategy_name"]
        closed_counts[strategy_name] = closed_counts.get(strategy_name, 0) + 1
        closed_net_pnl[strategy_name] = closed_net_pnl.get(strategy_name, 0.0) + float(trade["net_pnl"] or 0.0)

    for trade in open_trades:
        strategy_name = trade["strategy_name"]
        open_counts[strategy_name] = open_counts.get(strategy_name, 0) + 1

    execution_derived_session = compute_execution_derived_session_realized(all_execution_rows)

    last_row = rows[-1] if rows else None
    raw_runtime_statuses, runtime_status_summary = (
        load_runtime_statuses(runtime_status_dir, target_strategies)
        if runtime_status_dir
        else (
            [],
            {
                "directory_exists": False,
                "directory_path": None,
                "files_seen": 0,
                "files_loaded": 0,
                "non_object_count": 0,
                "parse_error_count": 0,
                "non_target_strategy_count": 0,
                "missing_identity_count": 0,
                "missing_heartbeat_count": 0,
                "future_heartbeat_count": 0,
                "stale_heartbeat_count": 0,
                "latest_seen_heartbeat_utc": None,
                "latest_loaded_heartbeat_utc": None,
                "latest_file_mtime_utc": None,
            },
        )
    )
    runtime_statuses = build_hybrid_runtime_statuses(
        runtime_statuses=raw_runtime_statuses,
        execution_rows=rows,
        order_rows=order_rows,
        target_strategies=target_strategies,
        open_trade_keys={(trade["strategy_name"], trade["account_name"], trade["instrument_name"]) for trade in open_trades},
    )
    warnings: list[str] = []
    if runtime_status_dir and int(runtime_status_summary["files_seen"]) == 0:
        warnings.append("No runtime-status files were found in the configured runtime-status directory.")
    if int(runtime_status_summary["stale_heartbeat_count"]) > 0:
        warnings.append(
            f"Dropped {int(runtime_status_summary['stale_heartbeat_count'])} stale runtime-status file(s) because their heartbeats were older than {int(RUNTIME_STATUS_STALE_SECONDS)} seconds."
        )
    if int(runtime_status_summary["parse_error_count"]) > 0:
        warnings.append(
            f"Ignored {int(runtime_status_summary['parse_error_count'])} runtime-status file(s) because they could not be parsed as JSON."
        )
    if int(runtime_status_summary["missing_heartbeat_count"]) > 0:
        warnings.append(
            f"Ignored {int(runtime_status_summary['missing_heartbeat_count'])} runtime-status file(s) because they were missing last_heartbeat_utc."
        )
    if int(runtime_status_summary["future_heartbeat_count"]) > 0:
        warnings.append(
            f"Ignored {int(runtime_status_summary['future_heartbeat_count'])} runtime-status file(s) because their heartbeats were too far in the future."
        )
    payload = {
        "generated_at_utc": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "source": {
            "db_path": str(db_path),
            "target_strategies": target_strategies,
            "runtime_status_dir": str(runtime_status_dir) if runtime_status_dir else None,
            "last_execution_row_id": int(last_row["ExecutionRowId"]) if last_row else None,
            "last_execution_time_utc": nt_ticks_to_utc_iso(last_row["ExecutionTime"]) if last_row else None,
            "execution_rows_loaded": len(rows),
            "order_rows_loaded": len(order_rows),
            "runtime_status_summary": runtime_status_summary,
            "watch_mode_ready": True,
        },
        "summary": {
            "closed_trade_count": len(closed_trades),
            "open_trade_count": len(open_trades),
            "execution_count_by_strategy": execution_count_by_strategy,
            "closed_trade_count_by_strategy": closed_counts,
            "open_trade_count_by_strategy": open_counts,
            "closed_net_pnl_by_strategy": {
                strategy: round_money(value) for strategy, value in closed_net_pnl.items()
            },
            "execution_derived_session_date": execution_derived_session["session_date"],
            "execution_derived_session_closed_trade_count": execution_derived_session["closed_trade_count"],
            "execution_derived_session_realized_gross_pnl": execution_derived_session["realized_gross_pnl"],
            "execution_derived_session_realized_total_costs": execution_derived_session["realized_total_costs"],
            "execution_derived_session_realized_net_pnl": execution_derived_session["realized_net_pnl"],
            "execution_derived_session_realized_net_pnl_by_account": execution_derived_session[
                "realized_net_pnl_by_account"
            ],
            "runtime_status_count": len(runtime_statuses),
            "runtime_status_stale_dropped_count": int(runtime_status_summary["stale_heartbeat_count"]),
            "runtime_status_parse_error_count": int(runtime_status_summary["parse_error_count"]),
            "runtime_status_missing_heartbeat_count": int(runtime_status_summary["missing_heartbeat_count"]),
        },
        "closed_trades": closed_trades,
        "open_trades": open_trades,
        "runtime_statuses": runtime_statuses,
        "warnings": warnings,
        "notes": [
            "Trades are reconstructed directly from NinjaTrader execution rows.",
            "Each trade contains nested entry and exit executions so scale-ins, partial exits, and signal names stay visible.",
            "The file is designed to be rewritten atomically, making it safe for dashboards or other readers to poll.",
            "Strategy detection is taken from Strategy2Order when available and lightly falls back to known Hermes signal names.",
            "Runtime statuses are merged from strategy-authored JSON files when available.",
            "Broker acceptance and execution confirmation statuses are derived from NinjaTrader Orders and Executions tables.",
        ],
    }
    return payload


def write_snapshot(
    db_path: Path,
    output_path: Path,
    target_strategies: list[str],
    max_closed_trades: int,
    runtime_status_dir: Path | None = None,
) -> tuple[int | None, str | None]:
    payload = build_payload(
        db_path=db_path,
        target_strategies=target_strategies,
        max_closed_trades=max_closed_trades,
        runtime_status_dir=runtime_status_dir,
    )
    private_output_path = choose_private_output_path(output_path)
    atomic_write_json(private_output_path, payload)
    source = payload["source"]
    return source["last_execution_row_id"], source["last_execution_time_utc"], payload


def run_watch_loop(args: argparse.Namespace) -> None:
    db_path = Path(args.db_path).expanduser()
    output_path = Path(args.output).expanduser()
    log_path = Path(args.log_path).expanduser() if args.log_path else None
    target_strategies = list(dict.fromkeys(args.strategy or list(DEFAULT_STRATEGIES)))
    runtime_status_dir = (
        Path(args.runtime_status_dir).expanduser()
        if args.runtime_status_dir
        else choose_runtime_status_dir(output_path)
    )
    if not db_path.exists():
        raise FileNotFoundError(f"NinjaTrader DB not found: {db_path}")

    last_written_row_id: int | None = None
    append_log_line(log_path, f"Watcher starting for {output_path}")
    while True:
        try:
            row_id, row_time, payload = write_snapshot(
                db_path=db_path,
                output_path=output_path,
                target_strategies=target_strategies,
                max_closed_trades=int(args.max_closed_trades),
                runtime_status_dir=runtime_status_dir,
            )
            published_public = publish_public_snapshot(output_path, payload, log_path)
            if row_id != last_written_row_id:
                private_output_path = choose_private_output_path(output_path)
                message = (
                    f"wrote {private_output_path} "
                    f"(last_execution_row_id={row_id}, last_execution_time_utc={row_time})"
                )
                if published_public:
                    message += f" | published {output_path}"
                else:
                    message += f" | public publish deferred for {output_path}"
                print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {message}")
                append_log_line(log_path, message)
                last_written_row_id = row_id

            if not args.watch:
                return

            time.sleep(max(args.poll_seconds, 0.25))
        except KeyboardInterrupt:
            print("Stopped live trade export watcher.")
            append_log_line(log_path, "Watcher stopped by keyboard interrupt.")
            return
        except sqlite3.OperationalError as exc:
            print(f"SQLite read failed: {exc}")
            append_log_line(log_path, f"SQLite read failed: {exc}")
            if not args.watch:
                raise
            time.sleep(max(args.poll_seconds, 0.25))
        except Exception as exc:
            traceback_text = traceback.format_exc().strip()
            print(f"Unhandled exporter failure: {exc}")
            append_log_line(log_path, f"Unhandled exporter failure: {exc}")
            append_log_line(log_path, traceback_text)
            if not args.watch:
                raise
            time.sleep(max(args.poll_seconds, 0.25))


def main() -> None:
    run_watch_loop(parse_args())


if __name__ == "__main__":
    main()
