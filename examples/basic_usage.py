"""Basic screener-finance usage — mirrors the yfinance quickstart style."""
import screener_finance as sf

# Optional global config:
# sf.configure(delay=2.0, proxy="socks5://127.0.0.1:9050")

t = sf.Ticker("RELIANCE")            # consolidated (default)

print(t.info["name"], "| mcap:", t.info["market_cap"], "| PE:", t.info["stock_pe"])

print("\n-- Quarterly results (last 3 cols) --")
print(t.quarterly_results.iloc[:, -3:])

print("\n-- Annual P&L (last 3 cols) --")
print(t.profit_loss.iloc[:, -3:])

print("\n-- Shareholding (last 3 cols) --")
print(t.shareholding.iloc[:, -3:])

print("\n-- Pros --")
for p in t.pros:
    print(" +", p)
print("-- Cons --")
for c in t.cons:
    print(" -", c)

print("\n-- Peers (top 5) --")
print(t.peers.head(5))

print("\n-- Price history (experimental) --")
px = t.history("6m")
print(px.tail(3))

# exports
t.to_json("out/reliance.json")
t.to_csv("out/csv")
print("\nWrote out/reliance.json + out/csv/*.csv")
