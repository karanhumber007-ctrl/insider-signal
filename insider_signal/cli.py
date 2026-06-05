"""
insider-signal CLI — command line interface.

Usage:
    insider-signal scan NVDA
    insider-signal scan --smart-money
    insider-signal admin
    insider-signal person "scott-bessent"
"""

import logging
import click
from rich.console import Console
from rich.table import Table
from rich import box

from insider_signal.models import Tier, Action
from insider_signal.scoring import score_all, apply_convergence, ScoredTrade
from insider_signal.sources.openinsider import fetch_ticker as oi_fetch
from insider_signal.sources.capitoltrades import fetch_congressional_trades
from insider_signal.sources.unusualwhales import fetch_ticker as uw_fetch, fetch_latest
from insider_signal.sources.trumptracker import fetch_all_trades, fetch_cabinet_buys

console = Console()
logging.basicConfig(level=logging.WARNING)


def _tier_label(tier: Tier) -> str:
    labels = {
        Tier.TRUMP:      "[bold red]T1 TRUMP[/bold red]",
        Tier.CABINET:    "[bold orange1]T2 CABINET[/bold orange1]",
        Tier.POLITICIAN: "[bold yellow]T3 POLITICIAN[/bold yellow]",
        Tier.CORPORATE:  "[bold green]T4 CORPORATE[/bold green]",
    }
    return labels.get(tier, "UNKNOWN")


def _action_label(action: Action) -> str:
    if action == Action.BUY:
        return "[bold green]BUY[/bold green]"
    if action == Action.SELL:
        return "[bold red]SELL[/bold red]"
    return "UNKNOWN"


def _format_amount(lo, hi) -> str:
    if not lo:
        return "Unknown"
    if hi and hi != lo:
        return f"${lo:,.0f} – ${hi:,.0f}"
    return f"${lo:,.0f}"


def _render_table(scored: list[ScoredTrade], title: str) -> None:
    if not scored:
        console.print(f"\n[dim]No signals found for: {title}[/dim]\n")
        return

    table = Table(
        title=title,
        box=box.ROUNDED,
        show_lines=True,
        header_style="bold cyan",
    )

    table.add_column("Score",    style="bold white",  width=8)
    table.add_column("Signal",   width=16)
    table.add_column("Tier",     width=14)
    table.add_column("Insider",  width=22)
    table.add_column("Ticker",   width=8)
    table.add_column("Action",   width=8)
    table.add_column("Amount",   width=22)
    table.add_column("Date",     width=12)
    table.add_column("Source",   width=18)

    for st in scored:
        t = st.trade
        convergence_marker = " ⚡" if st.is_convergence else ""

        table.add_row(
            str(st.score),
            st.signal_label + convergence_marker,
            _tier_label(t.tier),
            t.insider_name,
            t.ticker,
            _action_label(t.action),
            _format_amount(t.amount_min, t.amount_max),
            t.trade_date,
            t.source.split("—")[0].strip(),
        )

    console.print(table)
    console.print(
        f"\n[dim]⚡ = convergence signal (same ticker, multiple sources)[/dim]\n"
    )


@click.group()
def cli():
    """insider-signal — multi-source insider trading intelligence."""
    pass


@cli.command()
@click.argument("ticker", required=False)
@click.option("--smart-money", is_flag=True,
              help="Scan all sources for $1M+ buys regardless of ticker.")
def scan(ticker, smart_money):
    """
    Scan insider trades for a ticker or run a smart-money sweep.

    Examples:\n
        insider-signal scan NVDA\n
        insider-signal scan --smart-money
    """
    if not ticker and not smart_money:
        console.print("[red]Provide a ticker or use --smart-money flag.[/red]")
        raise SystemExit(1)

    trades = []

    if smart_money:
        console.print("\n[bold cyan]Running smart-money sweep — $1M+ buys across all sources...[/bold cyan]\n")
        trades += fetch_latest()
        trades += fetch_cabinet_buys()
        trades += fetch_congressional_trades()
        trades = [t for t in trades if t.action == Action.BUY
                  and t.amount_min and t.amount_min >= 1_000_000]
        title = "Smart Money Sweep — $1M+ Buys"
    else:
        ticker = ticker.upper()
        console.print(f"\n[bold cyan]Scanning all sources for {ticker}...[/bold cyan]\n")
        trades += oi_fetch(ticker)
        trades += uw_fetch(ticker)
        trades += fetch_congressional_trades(ticker)
        trades += fetch_all_trades(ticker)
        title = f"Insider Signal Report — {ticker}"

    if not trades:
        console.print("[dim]No trades found.[/dim]")
        return

    scored = score_all(trades)
    scored = apply_convergence(scored)
    _render_table(scored, title)


@cli.command()
def admin():
    """
    Show latest Trump cabinet and administration trades.
    Tier 1 and Tier 2 signals only.

    Example:\n
        insider-signal admin
    """
    console.print("\n[bold red]Fetching Trump administration trades...[/bold red]\n")
    trades = fetch_cabinet_buys()

    if not trades:
        console.print("[dim]No cabinet trades found.[/dim]")
        return

    scored = score_all(trades)
    scored = apply_convergence(scored)
    _render_table(scored, "Administration Trades — Tier 1 & 2")


@cli.command()
@click.argument("name_slug")
def person(name_slug):
    """
    Fetch trades for a specific official by name slug.

    Example:\n
        insider-signal person scott-bessent\n
        insider-signal person nancy-pelosi
    """
    from insider_signal.sources.trumptracker import fetch_person
    from insider_signal.sources.capitoltrades import fetch_capitoltrades_ticker

    console.print(f"\n[bold cyan]Fetching trades for: {name_slug}[/bold cyan]\n")
    trades = fetch_person(name_slug)

    if not trades:
        console.print("[dim]No trades found for that name.[/dim]")
        return

    scored = score_all(trades)
    _render_table(scored, f"Trades — {name_slug}")


def main():
    cli()


if __name__ == "__main__":
    main()
