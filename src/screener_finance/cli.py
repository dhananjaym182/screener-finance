"""sfin — command line for screener-finance.

    sfin info SBIN
    sfin quarterly SBIN --csv out/
    sfin all SBIN --json sbin.json --csv-dir out/csv
    sfin batch --symbols-file nse.txt --out-dir out/bulk --fmt both
    sfin compare SBIN TCS INFY
    sfin history SBIN --period 5y --csv sbin_px.csv
"""
from __future__ import annotations

import json
import sys

try:
    import click
except ImportError as exc:  # pragma: no cover
    print("CLI needs click: pip install screener-finance", file=sys.stderr)
    raise SystemExit(2)

import screener_finance as sf


@click.group(context_settings=dict(max_content_width=110))
@click.version_option(sf.__version__, prog_name="sfin")
@click.option("--delay", type=float, default=None, help="Min seconds between requests (default 1.5)")
@click.option("--proxy", default=None, help="Proxy URL (http:// or socks5://)")
def cli(delay, proxy):
    """screener-finance CLI — yfinance-style data from Screener.in."""
    kwargs = {}
    if delay is not None:
        kwargs["delay"] = delay
    if proxy is not None:
        kwargs["proxy"] = proxy
    if kwargs:
        sf.configure(**kwargs)


@cli.command()
@click.argument("symbol")
@click.option("--view", type=click.Choice(["consolidated", "standalone"]), default="consolidated")
def info(symbol, view):
    """Print key metrics for SYMBOL."""
    t = sf.Ticker(symbol, view)
    click.echo(json.dumps(t.info, indent=1, ensure_ascii=False))


@cli.command()
@click.argument("symbol")
@click.option("--view", type=click.Choice(["consolidated", "standalone"]), default="consolidated")
def quarterly(symbol, view):
    """Print quarterly results as CSV to stdout."""
    df = sf.Ticker(symbol, view).quarterly_results
    click.echo(df.to_csv())


@cli.command(name="profit-loss")
@click.argument("symbol")
@click.option("--view", type=click.Choice(["consolidated", "standalone"]), default="consolidated")
def profit_loss(symbol, view):
    """Print annual P&L as CSV to stdout."""
    click.echo(sf.Ticker(symbol, view).profit_loss.to_csv())


@cli.command()
@click.argument("symbol")
@click.option("--view", type=click.Choice(["consolidated", "standalone"]), default="consolidated")
def balance(symbol, view):
    """Print balance sheet as CSV to stdout."""
    click.echo(sf.Ticker(symbol, view).balance_sheet.to_csv())


@cli.command()
@click.argument("symbol")
@click.option("--view", type=click.Choice(["consolidated", "standalone"]), default="consolidated")
def cashflow(symbol, view):
    """Print cash flow as CSV to stdout."""
    click.echo(sf.Ticker(symbol, view).cash_flow.to_csv())


@cli.command()
@click.argument("symbol")
@click.option("--view", type=click.Choice(["consolidated", "standalone"]), default="consolidated")
def shareholding(symbol, view):
    """Print shareholding pattern as CSV to stdout."""
    click.echo(sf.Ticker(symbol, view).shareholding.to_csv())


@cli.command()
@click.argument("symbol")
@click.option("--period", default="1y", show_default=True,
              type=click.Choice(["1m", "3m", "6m", "1y", "2y", "5y", "max"]))
@click.option("--csv", "csv_path", type=click.Path(), default=None)
def history(symbol, period, csv_path):
    """Print daily price history (CSV) for SYMBOL."""
    df = sf.Ticker(symbol).history(period)
    if csv_path:
        df.to_csv(csv_path)
        click.echo(f"saved -> {csv_path}")
    else:
        click.echo(df.to_csv())


@cli.command()
@click.argument("symbol")
@click.option("--json", "json_path", type=click.Path(), default=None)
@click.option("--csv-dir", type=click.Path(), default=None)
@click.option("--view", type=click.Choice(["consolidated", "standalone"]), default="consolidated")
def all(symbol, json_path, csv_dir, view):
    """Fetch everything for SYMBOL; optional JSON + CSV export."""
    t = sf.Ticker(symbol, view)
    if json_path:
        t.to_json(json_path)
        click.echo(f"JSON -> {json_path}")
    if csv_dir:
        paths = t.to_csv(csv_dir)
        click.echo(f"CSVs -> {len(paths)} files in {csv_dir}")
    if not json_path and not csv_dir:
        click.echo(json.dumps(t.raw, indent=1, ensure_ascii=False))


@cli.command()
@click.argument("symbols", nargs=-1, required=True)
@click.option("--out-dir", default="out/screener_finance", show_default=True)
@click.option("--fmt", type=click.Choice(["json", "csv", "both"]), default="both", show_default=True)
@click.option("--symbols-file", type=click.Path(exists=True), default=None)
def batch(symbols, out_dir, fmt, symbols_file):
    """Batch download with resume; SYMBOLS... or --symbols-file (one per line)."""
    syms = list(symbols)
    if symbols_file:
        with open(symbols_file, encoding="utf-8") as fp:
            syms += [ln.strip() for ln in fp if ln.strip() and not ln.startswith("#")]
    if not syms:
        raise click.UsageError("no symbols given")
    summary = sf.batch_download(syms, out_dir, fmt=fmt)
    click.echo(json.dumps(summary, indent=1))


@cli.command()
@click.argument("symbols", nargs=-1, required=True)
def compare(symbols):
    """Side-by-side key metrics for several symbols."""
    click.echo(sf.compare(list(symbols)).to_csv())


@cli.command()
@click.argument("query")
@click.option("--limit", default=10, show_default=True)
def search(query, limit):
    """Search companies by name/symbol."""
    for r in sf.search(query, limit):
        click.echo(f"{r['symbol']:<15} {r['name']}")


def main() -> int:
    try:
        cli()
    except sf.ScreenerError as exc:
        click.echo(f"error: {exc}", err=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
