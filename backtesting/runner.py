"""CLI runner for backtesting: python -m backtesting.runner --start 2024-01-01 --end 2024-12-31"""
import argparse
import asyncio
from backtesting.engine import BacktestEngine, BacktestConfig


def main() -> None:
    parser = argparse.ArgumentParser(description="MultiAgent-FX-Engine Backtester")
    parser.add_argument("--symbol", default="EURUSD=X")
    parser.add_argument("--start", default="2024-01-01")
    parser.add_argument("--end", default="2024-12-31")
    parser.add_argument("--balance", type=float, default=10_000.0)
    parser.add_argument("--risk", type=float, default=1.0, help="Risk per trade %%")
    parser.add_argument("--sl-mult", type=float, default=1.5)
    parser.add_argument("--tp-mult", type=float, default=2.5)
    parser.add_argument("--max-dd", type=float, default=2.0, help="Max daily drawdown %%")
    args = parser.parse_args()

    config = BacktestConfig(
        symbol=args.symbol,
        start_date=args.start,
        end_date=args.end,
        initial_balance=args.balance,
        risk_per_trade_pct=args.risk,
        sl_multiplier=args.sl_mult,
        tp_multiplier=args.tp_mult,
        max_daily_drawdown_pct=args.max_dd,
    )

    print(f"\nRunning backtest: {config.symbol} | {config.start_date} -> {config.end_date}")
    print(f"Balance: ${config.initial_balance:,.2f} | Risk: {config.risk_per_trade_pct}%\n")

    engine = BacktestEngine(config)
    result = asyncio.run(engine.run())
    result.print_report()


if __name__ == "__main__":
    main()
