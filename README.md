# insider-signal

> Multi-source insider trading intelligence CLI for retail investors.

![CI](https://github.com/karanhumber007-ctrl/insider-signal/actions/workflows/ci.yml/badge.svg)
![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)
![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)

Aggregates U.S. government insider trading disclosures from four
public sources into a single tiered signal layer — from one command.

No equivalent open-source aggregator exists.

---

## Why this exists

Retail developers building algorithmic trading systems or financial
transparency tools currently have no clean Python library to pull
these signals together. They check four different websites manually,
with no deduplication or unified scoring.

`insider-signal` is the infrastructure layer that eliminates that gap.

---

## Data Sources

| Tier | Who | Source | Data Type |
|------|-----|--------|-----------|
| T1 | President / Vice President | TrumpTracker | OGE filings |
| T2 | Cabinet secretaries | TrumpTracker | OGE filings |
| T3 | Congress (House + Senate) | CapitalTrades + Gov JSON feeds | STOCK Act |
| T4 | CEOs, CFOs, Directors | OpenInsider + UnusualWhales | SEC Form 4 |

All sources are **public government disclosures**. No paywalls. No API keys required.

---

## Install

```bash
pip install insider-signal
```

---

## Usage

```bash
# Full 4-tier report for a ticker
insider-signal scan NVDA

# Smart money sweep — $1M+ buys across all sources
insider-signal scan --smart-money

# Latest Trump cabinet trades only
insider-signal admin

# Trades for a specific person
insider-signal person scott-bessent
insider-signal person nancy-pelosi
```

---

## Signal Scoring

Every trade is scored across five dimensions:

| Dimension | Logic |
|-----------|-------|
| Tier weight | Cabinet = 8.0x, Politician = 5.0x, Corporate = 3.0x |
| Action | BUY = 1.0x multiplier, SELL = 0.3x |
| Trade size | $1M+ = +3.0, $10M+ = +4.0 |
| Disclosure lag | Filed late = penalty up to -2.0 |
| 10b5-1 penalty | Pre-scheduled sales = -3.0 |

**Convergence bonus (+3.0):** Same ticker appearing across two or
more sources within the same window — the strongest signal in the tool.

---

## Project Structure
insider_signal/
├── sources/
│   ├── openinsider.py      # SEC Form 4
│   ├── capitoltrades.py    # STOCK Act — House, Senate
│   ├── unusualwhales.py    # Options flow + insider cross-ref
│   └── trumptracker.py     # Executive branch OGE filings
├── models.py               # Trade dataclass, Tier, Action enums
├── scoring.py              # Signal scoring + convergence engine
└── cli.py                  # Click CLI — scan, admin, person
---

## Contributing

Pull requests welcome. Please open an issue first for major changes.

---

## License

MIT — see [LICENSE](LICENSE)
