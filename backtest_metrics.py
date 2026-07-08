"""
Performance statistics for a backtest_engine.BacktestResult.
"""


def summarize(trades, equity_curve, initial_balance):
    """Return a dict of summary performance stats for a completed backtest."""
    if not trades:
        return {
            "num_trades": 0,
            "win_rate": None,
            "total_return_pct": 0.0,
            "max_drawdown_pct": 0.0,
            "profit_factor": None,
            "avg_r_multiple": None,
            "final_balance": initial_balance,
        }

    pnls = [t.total_pnl for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]

    final_balance = initial_balance + sum(pnls)

    running_max = equity_curve.cummax()
    drawdown = (equity_curve - running_max) / running_max
    max_drawdown_pct = drawdown.min() * 100 if not drawdown.empty else 0.0

    gross_profit = sum(wins)
    gross_loss = -sum(losses)
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else float("inf")

    return {
        "num_trades": len(trades),
        "win_rate": len(wins) / len(trades),
        "total_return_pct": (final_balance - initial_balance) / initial_balance * 100,
        "max_drawdown_pct": max_drawdown_pct,
        "profit_factor": profit_factor,
        "avg_r_multiple": sum(t.r_multiple for t in trades) / len(trades),
        "final_balance": final_balance,
    }


def format_report(symbol, start, end, stats):
    """Human-readable summary block for CLI output."""
    lines = [
        f"Backtest report: {symbol} | {start:%Y-%m-%d} -> {end:%Y-%m-%d}",
        "-" * 60,
        f"Trades:            {stats['num_trades']}",
    ]

    if stats["num_trades"] == 0:
        lines.append("No trades were triggered in this window.")
        return "\n".join(lines)

    lines.extend(
        [
            f"Win rate:          {stats['win_rate'] * 100:.1f}%",
            f"Total return:      {stats['total_return_pct']:+.2f}%",
            f"Max drawdown:      {stats['max_drawdown_pct']:.2f}%",
            f"Profit factor:     {stats['profit_factor']:.2f}",
            f"Avg R-multiple:    {stats['avg_r_multiple']:+.2f}R",
            f"Final balance:     {stats['final_balance']:.2f}",
        ]
    )
    return "\n".join(lines)
