# QuantDSS Architecture

> Quantitative Decision Support & Execution System for Intraday Indian Equity Markets

## System Overview

QuantDSS is an **automated intraday trading platform** for Indian equities (NSE).
It ingests live market data, evaluates quantitative strategies, filters signals
through an 11-layer intelligence pipeline, validates risk, and executes trades
via broker APIs — all in real time.

```
Broker APIs ──► Tick Ingestion ──► Candle Aggregation ──► Strategy Evaluation
                                                                │
                                  ┌─────────────────────────────┘
                                  ▼
                        Intelligence Pipeline (11 layers)
                                  │
                                  ▼
                        Risk Engine (17 rules)
                                  │
                              ┌───┴───┐
                              ▼       ▼
                          AutoTrader  UI (SSE)
                          (Paper/Live)
```

---

## Core Components

### 1. Market Data Ingestion

- **WebSocket Manager** — connects to Upstox/Angel One for live tick data
- **Candle Aggregator** — builds 1-minute OHLCV candles from ticks, publishes to Redis Stream `market:candles`
- **Market Data Cache** — in-memory LTP, volume, and spread cache

### 2. Strategy Engine

- **Strategy Runner** — loads strategies from DB, evaluates each against candle DataFrames
- **Base Strategy** — abstract class; strategies produce `CandidateSignal` objects only (no DB/broker access)
- **Strategies**: EMA Crossover, RSI Mean Reversion, ORB+VWAP, Volume Expansion, Trend Continuation, VWAP Reclaim, Relative Strength, Trend Pullback

### 3. Signal Intelligence Pipeline (11 Layers)

All signals pass through these layers in order. No bypass is permitted.

| #   | Layer                    | Module                      | Purpose                                                    |
| --- | ------------------------ | --------------------------- | ---------------------------------------------------------- |
| 1   | Signal Deduplication     | `signal_dedup.py`           | Prevent duplicate signals within TTL window                |
| 2   | Signal Pool              | `signal_pool.py`            | Buffer + group signals by symbol                           |
| 3   | Consolidation            | `consolidation_layer.py`    | Merge concurrent signals, resolve conflicts                |
| 4   | **Meta-Strategy Engine** | `meta_strategy_engine.py`   | Block disabled strategies + regime-incompatible strategies |
| 5   | Confirmation             | `confirmation_layer.py`     | Require multi-strategy alignment                           |
| 6   | Quality Score            | `quality_score_layer.py`    | Score on volume, trend, VWAP, spread                       |
| 7   | **Market Regime Filter** | `market_regime_filter.py`   | Block signals incompatible with current regime             |
| 8   | ML Filter                | `ml_filter_layer.py`        | Win probability prediction (shadow mode)                   |
| 9   | NLP Filter               | `nlp_filter_layer.py`       | News sentiment check (shadow mode)                         |
| 10  | Time Filter              | `time_filter_layer.py`      | Enforce trading time windows                               |
| 11  | **Liquidity Filter**     | `liquidity_filter_layer.py` | Minimum volume ratio + max spread check                    |

### 4. Risk Engine (17 Rules)

- Located in `risk_engine.py`, called by `FinalAlertGenerator`
- Fail-fast ordered chain: daily loss, drawdown, cooldown, volatility, position sizing, max positions, duplicate, consecutive loss, weekly loss, and more
- Returns `RiskDecision` with status (APPROVED / BLOCKED / SKIPPED), quantity, and risk amount

### 5. Final Alert Generator

- Terminus of the intelligence pipeline
- Calls Risk Engine (mandatory), persists all signals to DB, publishes via SSE, enqueues APPROVED signals to AutoTrader queue

### 6. AutoTrader

- **Reactive mode**: processes signals from the AutoTrader queue (fed by FinalAlertGenerator)
- **Scheduled mode**: scans watchlist every 5 min, routes signals through the intelligence pipeline (not bypassed)
- Supports both **paper** and **live** trading modes
- Live mode uses `ExecutionManager` for bracket orders via broker API

### 7. Supporting Components

- **Strategy Health Monitor** — tracks win rate per strategy, auto-disables poor performers
- **Regime Detector** — classifies market as TREND / RANGE / HIGH_VOLATILITY / LOW_LIQUIDITY
- **Signal Tracer** — 18-stage trace IDs for debugging signal flow
- **Candle Consumer** — Redis Stream consumer that feeds strategy evaluation

---

## Data Flow

```
1. Ticks arrive via WebSocket
2. CandleAggregator → 1-min candles → Redis Stream
3. CandleConsumer reads stream → builds DataFrame → StrategyRunner
4. Strategies produce CandidateSignals
5. SignalDedup → SignalPool → Consolidation → MetaStrategy
   → Confirmation → QualityScore → RegimeFilter
   → ML → NLP → TimeFilter → LiquidityFilter
6. FinalAlertGenerator → RiskEngine validation
7. APPROVED → AutoTraderQueue → _open_trade (paper or live)
   BLOCKED  → persisted + SSE notification
```

---

## Deployment

- **Monolith mode** (`WORKER_MODE=monolith`): all components in one process
- **Distributed mode**: CandleConsumer runs as a separate service

---

## Key Design Rules

1. **Single pipeline** — every signal (scanner, scheduled, real-time) passes through all 11 intelligence layers
2. **Risk Engine is mandatory** — no trade executes without Risk Engine validation
3. **Strategy isolation** — strategies only return signals, no side effects
4. **Meta-Strategy control** — disabled/regime-blocked strategies are filtered before confirmation
5. **Signal traceability** — every signal gets a trace_id tracked across 18 stages
