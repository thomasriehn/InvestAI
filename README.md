# InvestAI

Autonomer Multi-Agent zur Analyse von **Aktien, Indizes und Fonds**, mit
Dauerbeobachtung des Marktes, Prognosen aus Vergangenheitsentwicklungen und
einem **Demo-Depot über 10'000 CHF**, das alle Kauf-/Verkaufsempfehlungen
automatisch ausführt. Die Prognosen optimieren sich rückblickend selbst.

## Architektur

```
┌──────────────────┐   ┌──────────────────┐   ┌────────────────────┐
│ MarketData       │ → │ ForecastEngine   │ → │ PortfolioEngine    │
│ (yfinance+SQLite)│   │ (Ridge/RF/GBR    │   │ (10'000 CHF demo,  │
│                  │   │  Ensemble)       │   │  fees + risk caps) │
└──────────────────┘   └──────────────────┘   └────────────────────┘
        ▲                       ▲                       │
        │                       │                       ▼
        │              ┌───────────────────┐    ┌────────────────────┐
        │              │ Backtester        │    │ portfolio_history  │
        │              │ (walk-forward)    │    │ + trades + PnL     │
        │              └─────────┬─────────┘    └────────────────────┘
        │                        │
        │              ┌───────────────────┐
        │              │ AutoOptimizer     │  ← random search,
        │              │ persists champion │     evaluated by Sharpe/
        │              │ params per model  │     hit-rate / MAE
        │              └─────────┬─────────┘
        │                        │
┌───────┴───────────────────────────────────────────────────────────┐
│ Orchestrator.cycle() → DataAgent → ForecastAgent → PortfolioAgent │
│        (alle 15 Minuten / off-hours stündlich)                    │
└───────────────────────────────────────────────────────────────────┘
```

## Installation

```bash
pip install -e .
# oder
pip install -r requirements.txt
```

Python ≥ 3.10. Marktdaten kommen von `yfinance` (kostenlos, kein API-Key).
Falls das Netzwerk nicht erreichbar ist, fällt das System auf einen
deterministischen Synthetik-Generator zurück (`--synthetic`), damit alle
Komponenten lokal getestet werden können.

## CLI

```bash
investai data refresh          # Kurse laden / aktualisieren
investai data status           # Cache-Status
investai forecast run          # Prognosen erstellen + persistieren
investai forecast show NESN.SW # Prognosen für ein Ticker einsehen
investai portfolio show        # Aktueller Depotstand
investai portfolio rebalance   # 1 Zyklus: Daten -> Prognose -> Trades
investai portfolio history     # Equity-Kurve
investai portfolio trades      # Tradeliste
investai portfolio reset --yes # Depot zuruecksetzen
investai backtest run          # Walk-Forward-Backtest
investai optimize run          # Hyperparameter neu optimieren + speichern
investai watch --cycles 0 --interval 900   # Dauerbeobachtung
investai info                  # Uebersicht
```

## Multi-Agent-Setup (Claude Code)

Jeder wiederkehrende Schritt ist als Claude-Subagent in `.claude/agents/`
hinterlegt:

- `market-data-collector`     - refresh universe
- `forecast-generator`        - neue Prognosen
- `portfolio-manager`         - BUY/SELL anwenden
- `backtest-runner`           - Walk-Forward-Backtests
- `prediction-optimizer`      - Hyperparameter retunen
- `portfolio-reporter`        - Performance-Report
- `observer-runner`           - Dauerbeobachter starten

Nutzungsbeispiel im Chat:

> *"Run the observer for 4 cycles, then have the portfolio-reporter summarise."*

Claude waehlt die passenden Subagents automatisch.

## Selbst-Optimierung

Jede Prognose wird mit `made_on`, `target_date`, `expected_return`,
`direction_prob` und einem Snapshot der Features in `predictions` gespeichert.
Sobald das `target_date` erreicht ist, wird der **realisierte Return** vom
Orchestrator in `realized_return`/`error` zurueckgeschrieben.

Der `AutoOptimizer` fuehrt Random-Search ueber drei Modellklassen
(Ridge, RandomForest, GradientBoosting) und bewertet jeden Kandidaten via
Walk-Forward-Backtest auf einem Multi-Ticker-Panel. Die jeweils beste
Konfiguration landet in `model_params`; **alle nachfolgenden Forecasts laden
automatisch die aktuellen Parameter** - dadurch verbessert sich die Pipeline
ueber die Zeit ohne manuelle Eingriffe.

`config/settings.yaml` steuert wie oft optimiert wird (`reoptimize_every_runs`).

## Demo-Depot (10'000 CHF)

- Startkapital 10'000 CHF (CHF ist Basiswaehrung; FX-Konvertierung ueber Yahoo
  Pairs `CHF=X`, `EURCHF=X`, ...).
- Maximal 20 % je Position, mind. 5 % Cash-Reserve, Mindesthandel 100 CHF.
- Realistische Schweizer Brokergebuehr (0.1 %, mind. 1 CHF).
- Trades werden mit Begruendung (z. B. *"forecast +2.3 %, up_prob 71 %"*) in
  `trades` gespeichert; Equity-Snapshots laufen in `portfolio_history`.

## Konfiguration

Universum (`config/universe.yaml`) und Risiko-/Forecast-Parameter
(`config/settings.yaml`) sind editierbar - keine Code-Aenderungen noetig.

## Tests

```bash
pytest -q
```
