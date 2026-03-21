"""
execution/oanda.py — OANDA REST v20 Broker Adapter.
Full implementation using oandapyV20 library.
Requires OANDA_API_KEY, OANDA_ACCOUNT_ID, OANDA_ENVIRONMENT in .env
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import List, Optional

from execution.broker_interface import (
    AccountInfo, BrokerInterface, CloseResult, OrderResult, Position
)
from utils.logger import get_logger

logger = get_logger("oanda_broker")


class OANDABroker(BrokerInterface):
    """OANDA REST v20 adapter. Requires oandapyV20 package."""
    broker_name = "oanda"

    def __init__(self, api_key: str, account_id: str, environment: str = "practice"):
        try:
            import oandapyV20
            import oandapyV20.endpoints.accounts as accounts
            import oandapyV20.endpoints.orders as orders
            import oandapyV20.endpoints.trades as trades_ep
            import oandapyV20.endpoints.pricing as pricing

            self._oanda = oandapyV20
            self._accounts = accounts
            self._orders = orders
            self._trades_ep = trades_ep
            self._pricing = pricing

            self._api = oandapyV20.API(
                access_token=api_key,
                environment=environment,
            )
            self._account_id = account_id
            self._connected = True
            logger.info(f"OANDA broker initialized [{environment}] account={account_id}")
        except ImportError:
            logger.error("oandapyV20 not installed. Run: pip install oandapyV20")
            self._connected = False
        except Exception as e:
            logger.error(f"OANDA init failed: {e}")
            self._connected = False

    def is_connected(self) -> bool:
        if not self._connected:
            return False
        try:
            r = self._accounts.AccountSummary(self._account_id)
            self._api.request(r)
            return True
        except Exception:
            return False

    def get_account_info(self) -> AccountInfo:
        try:
            r = self._accounts.AccountSummary(self._account_id)
            self._api.request(r)
            acc = r.response["account"]
            return AccountInfo(
                account_id=self._account_id,
                balance=float(acc["balance"]),
                equity=float(acc["NAV"]),
                unrealized_pnl=float(acc["unrealizedPL"]),
                margin_used=float(acc["marginUsed"]),
                margin_available=float(acc["marginAvailable"]),
                currency=acc["currency"],
                broker="oanda",
            )
        except Exception as e:
            logger.error(f"OANDA get_account_info failed: {e}")
            raise

    def get_price(self, symbol: str) -> tuple[float, float]:
        """Get current bid/ask. symbol format: EUR_USD"""
        try:
            r = self._pricing.PricingInfo(
                accountID=self._account_id,
                params={"instruments": symbol}
            )
            self._api.request(r)
            price_data = r.response["prices"][0]
            bid = float(price_data["bids"][0]["price"])
            ask = float(price_data["asks"][0]["price"])
            return bid, ask
        except Exception as e:
            logger.error(f"OANDA get_price failed: {e}")
            raise

    def place_order(
        self,
        symbol: str,
        side: str,
        units: float,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
    ) -> OrderResult:
        """Place a market order with optional SL/TP."""
        signed_units = units if side.upper() == "BUY" else -units

        order_data: dict = {
            "order": {
                "type": "MARKET",
                "instrument": symbol,
                "units": str(int(signed_units)),
                "timeInForce": "FOK",
                "positionFill": "DEFAULT",
            }
        }

        if stop_loss:
            order_data["order"]["stopLossOnFill"] = {"price": f"{stop_loss:.5f}"}
        if take_profit:
            order_data["order"]["takeProfitOnFill"] = {"price": f"{take_profit:.5f}"}

        try:
            r = self._orders.OrderCreate(self._account_id, data=order_data)
            self._api.request(r)
            resp = r.response

            if "orderFillTransaction" in resp:
                fill = resp["orderFillTransaction"]
                return OrderResult(
                    order_id=fill.get("orderID", ""),
                    trade_id=fill.get("tradeOpened", {}).get("tradeID", str(uuid.uuid4())),
                    symbol=symbol,
                    side=side,
                    units=abs(float(fill.get("units", signed_units))),
                    fill_price=float(fill.get("price", 0)),
                    stop_loss=stop_loss,
                    take_profit=take_profit,
                    timestamp=datetime.now(timezone.utc),
                    status="FILLED",
                )
            else:
                return OrderResult(
                    order_id="",
                    trade_id="",
                    symbol=symbol,
                    side=side,
                    units=units,
                    fill_price=0,
                    stop_loss=stop_loss,
                    take_profit=take_profit,
                    timestamp=datetime.now(timezone.utc),
                    status="REJECTED",
                    error=str(resp),
                )
        except Exception as e:
            logger.error(f"OANDA place_order failed: {e}")
            return OrderResult(
                order_id="", trade_id="", symbol=symbol, side=side,
                units=units, fill_price=0, stop_loss=stop_loss, take_profit=take_profit,
                timestamp=datetime.now(timezone.utc), status="REJECTED", error=str(e)
            )

    def close_position(self, trade_id: str) -> CloseResult:
        try:
            r = self._trades_ep.TradeClose(self._account_id, trade_id)
            self._api.request(r)
            close_tx = r.response.get("orderFillTransaction", {})
            pnl = float(close_tx.get("pl", 0))
            price = float(close_tx.get("price", 0))
            return CloseResult(
                trade_id=trade_id,
                realized_pnl=pnl,
                close_price=price,
                timestamp=datetime.now(timezone.utc),
                status="CLOSED",
            )
        except Exception as e:
            logger.error(f"OANDA close_position failed: {e}")
            return CloseResult(
                trade_id=trade_id, realized_pnl=0, close_price=0,
                timestamp=datetime.now(timezone.utc), status="ERROR", error=str(e)
            )

    def get_open_positions(self) -> List[Position]:
        try:
            r = self._trades_ep.OpenTrades(self._account_id)
            self._api.request(r)
            positions = []
            for t in r.response.get("trades", []):
                units = float(t.get("currentUnits", 0))
                side = "BUY" if units > 0 else "SELL"
                positions.append(Position(
                    trade_id=t.get("id", ""),
                    symbol=t.get("instrument", ""),
                    side=side,
                    units=abs(units),
                    entry_price=float(t.get("price", 0)),
                    current_price=float(t.get("price", 0)),
                    unrealized_pnl=float(t.get("unrealizedPL", 0)),
                    stop_loss=float(t["stopLossOrder"]["price"]) if "stopLossOrder" in t else None,
                    take_profit=float(t["takeProfitOrder"]["price"]) if "takeProfitOrder" in t else None,
                    open_time=datetime.now(timezone.utc),
                ))
            return positions
        except Exception as e:
            logger.error(f"OANDA get_open_positions failed: {e}")
            return []
