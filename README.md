<div align="center">
  <h1>📈 QuantDSS</h1>
  <p><strong>Quantitative Decision Support & Execution System for Intraday Indian Equity Markets</strong></p>
</div>

> **QuantDSS is a discipline-enforcement tool. It does not predict the market. It enforces the rules you already know but fail to follow.**

## 📖 Overview

QuantDSS is a self-hosted, highly sophisticated, zero-cost trading decision support API and dashboard for Indian NSE/BSE cash equities. It acts as an automated intraday trading platform that ingests live market data, evaluates quantitative strategies, filters signals through a rigorous **11-layer intelligence pipeline**, validates risk against **17 hard rules**, and executes trades via broker APIs — all in real time and without emotion.

## ✨ Key Features

- **Live Market Data Ingestion:** Connects directly to broker WebSockets (Upstox/Angel One) for live tick data with dual-feed failover, aggregating ticks into 1-minute OHLCV candles, and maintains an in-memory LTP cache.
- **Advanced Strategy Engine:** Evaluates multiple parallel strategies including EMA Crossover, RSI Mean Reversion, ORB+VWAP, Volume Expansion, Trend Continuation, VWAP Reclaim, and more.
- **11-Layer Signal Intelligence Pipeline:** A mandatory, fail-fast filtering system that every generated signal must pass through. Features Signal Deduplication, Meta-Strategy Blocking, Confirmation, Quality Scoring, Market Regime Formatting, ML/NLP Predictions, Time Window constraints, and Liquidity checks.
- **Uncompromising Risk Engine:** 17 hard risk rules enforcing max daily loss, peak-to-trough drawdowns, post-loss cooldowns, volatility checks, position sizing parameters, max open positions, and consecutive losses. **No signal bypasses the Risk Engine.** Includes a **Risk Reservation Layer** with distributed locks to strictly prevent concurrent signals from breaching limits.
- **AutoTrader Execution:** Supports event-driven reactive routing and scheduled scanning modes. Seamlessly switch between **Paper Trading** and **Live Execution** modes using Broker APIs (Shoonya, Upstox, Angel One) with persistent toggle states via local storage. 
- **Modern Real-Time Dashboard:** Next.js + TailwindCSS UI hooked up to FastAPI via Server-Sent Events (SSE) for millisecond-level reaction times and visualizations.
- **Self-Healing Reliability & Chaos-Tested:** Distributed locking for portfolio risk, shared API error counters, session-isolated retries, zombie position handling, and auto-recovery from infrastructure crashes (Redis memory exhaustion, broker failures). Extensively chaos tested.
- **Net PnL Circuit Breakers:** Real-time calculation of Net PnL (deducting estimated slippage and fees) to accurately trigger safety halts.
- **Tick Data Lake & Replay Engine:** Stores raw market ticks allowing full deterministic simulation platforms with historical data loaders, transaction cost modeling, and pipeline replay.
- **Horizontal Signal Engine Sharding:** Deterministic hashing assigns symbols to dynamically scalable workers for linear throughput scaling.
- **Kafka & Redis Streams Pipeline:** High-throughput streaming supporting dual-write mode (Redis + Kafka) for zero-downtime message propagation.

## 🛡️ Audits & System Validation

QuantDSS has undergone rigorous enterprise-grade auditing and chaos testing:
- **God Mode Repository Audit (Passed):** A full-stack wiring integrity audit validating that every UI button, API endpoint, database schema, and background worker is functional and properly connected.
- **17-Phase Runtime Simulation (Passed):** An exhaustive runtime stress test validating system safety under real-world conditions, including market data floods, rapid signal generation, rate limit storms, worker crashes (PEL recovery), broker API HTTP timeouts, global kill switch triggers, partial fills, and EOD square-offs.
- **Current Classification:** 🔥 **READY FOR LIVE TRADING (PAPER RECOMMENDED FIRST FOR 30 DAYS)**

## 🛠️ Tech Stack

| Component | Technology |
| --- | --- |
| **Frontend** | [Next.js 14](https://nextjs.org/) + TypeScript + [shadcn/ui](https://ui.shadcn.com/) + TailwindCSS |
| **Backend** | [FastAPI](https://fastapi.tiangolo.com/) (Python 3.11) + Uvicorn + Polars / NumPy |
| **Database & Feature Store** | PostgreSQL 15 + TimescaleDB 2.x |
| **Message Broker (PubSub)** | Redis 7 & Apache Kafka |
| **Broker Integration** | Shoonya (Finvasia), Upstox (Primary), Angel One (Failover) |
| **Historical Data** | yfinance |
| **Notifications & Observability** | Telegram Bot API, Prometheus, Grafana |
| **Infrastructure** | Docker Compose |

---

## 🏗️ Architecture & Data Flow

QuantDSS follows a fully decoupled, stream-based architecture designed for high availability and low latency.

```mermaid
graph TD;
    BrokerAPIs[Upstox/AngelOne WebSockets] --> TickNorm[Tick Ingestion & Normalization]
    TickNorm --> CandleAggr[Candle Aggregator]
    CandleAggr --> RedisStream[(Kafka / Redis `market:candles`)]
    
    RedisStream --> CandleConsumer[Candle Consumer / Worker Shards]
    CandleConsumer --> StrategyRunner[Strategy Evaluator & Indicator Engine]
    
    subgraph 11-Layer Intelligence Pipeline
        StrategyRunner --> Deduplication --> SignalPool --> Consolidation --> MetaStrategy
        MetaStrategy --> Confirmation --> QualityScore --> RegimeFilter --> ML_NLP_Filter --> Time_Liquidity_Filter
    end
    
    Time_Liquidity_Filter --> FinalAlert[Final Alert Generator]
    FinalAlert --> RiskEngine{Risk Engine - 17 Rules}
    
    RiskEngine -- BLOCKED / SKIPPED --> DB[(TimescaleDB / PostgreSQL)]
    RiskEngine -- APPROVED --> AutoTraderQueue[AutoTrader Queue]
    
    AutoTraderQueue --> AutoTrader[AutoTrader Engine]
    AutoTrader --> PaperTrading[Paper Trading Monitor]
    AutoTrader --> LiveExecution[Live Execution Manager]
    LiveExecution --> Broker[Live Broker Orders]
    
    DB --> SSEStreamer[SSE Streamer]
    SSEStreamer --> WebUI[Next.js Dashboard]
    FinalAlert --> Telegram[Telegram Bot]
    
    subgraph Observability, Self-Healing & Data
        CandleConsumer --> MLStore[(Feature Store)]
        Broker --> TradeMonitor[Trade Monitor Worker]
        TradeMonitor --> Recon[Position Reconciliation]
        RedisStream --> PELRecovery[PEL Recovery Worker]
    end
```

---

## 🏛️ Comprehensive Layer Architecture

### 1. Data Ingestion Layer & Dual Feed
Market data enters via **Upstox WebSocket Full-C stream** (primary) and **AngelOne** (secondary).
- **Failover**: If Upstox is stale for >3 seconds, it switches to AngelOne.
- **Tick Normalization**: Tracks per-tick delta volumes mathematically to reconstruct VTT without phantom volume spikes. Data flows into the **Tick Data Lake** for precise backtesting.
- **Cache & Aggregation**: In-memory cache tracking LTP, Bid/Ask, and Spread. Ticks are converted into 1-minute OHLCV candles and pushed directly to `market:candles` on Redis and Kafka.
- **Gap Recovery**: If WebSockets disconnect, upon reconnect, the system queries REST APIs for 1-minute historical candles to backfill gaps dynamically.

### 2. Multi-Timeframe Engine
Builds hierarchical timeframes directly from lower-level data:
- Ticks construct `1s`, `5s`, `15s`, `30s`, `1m` candles.
- `1m` dynamically scales to `5m` and higher. Strategies request precise timeframes, executing only when that timeframe closes.

### 3. Indicator Engine
Utilizes `pandas`, `NumPy`, and `ta` for ultra-fast C-bound zero-overhead calculation (<50ms compute).
Supports: EMA/SMA, RSI, ATR, MACD, Volume MA, VWAP (anchored, auto-resetting), Bollinger Bands, ORB (Opening Range Breakout).

### 4. Strategy Engine
Strategies run immutably, generating `CandidateSignal` arrays without side effects or DB calls:
- **Available Strategies:** EMA Crossover, RSI Mean Reversion, ORB+VWAP Breakout, Volume Expansion, Trend Continuation, Failed Breakout, VWAP Reclaim, Trend Pullback, Relative Strength.

### 5. Meta Strategy Engine (Intelligence Layer 4)
Controls gates dynamically:
- **Strategy Performance Monitoring**: Analyzes trailing 7-day win rates. Disables underperforming strategies automatically.
- **Regime Gating**: Enforces regime gating (blocking mean reversion in trending markets).

### 6. 11-Layer Intelligence Pipeline
Every signal passes through sequentially. Split into Fast Path (<200ms) and Slow Path (async shadow).
1. **Signal Deduplication**: Blocks identical signals via TTL window, expanded for safety.
2. **Signal Pool**: Buffers concurrent signals.
3. **Consolidation**: Merges simultaneous signals, resolving Long vs Short conflicts.
4. **Meta-Strategy**: Blocks under-performing regimes automatically.
5. **Confirmation**: Multi-indicator & strategy alignment requirement.
6. **Quality Score**: 0-100 tier assignment based on VWAP divergence, Trend, and Volume relative impact.
7. **Market Regime Filter**: Re-evaluates target symbol topology (Trend/Range/High Volatility).
8. **ML Filter (Slow Path)**: Target win-probability projection via Random Forests.
9. **NLP Filter (Slow Path)**: Streaming sentiment analysis of breaking news.
10. **Time Filter**: Intraday permissibility window (e.g., 09:20–14:30 IST).
11. **Liquidity Filter**: Enforces ADV minimums and strict Bid/Ask spread constraints.

Terminates at **Final Alert Generator**, pushing the candidate to the Risk Engine.

### 7. Risk Engine (17 Strict Rules) & Reservation Layer
A fail-fast execution sequence preventing catastrophic ruin. Includes a **Risk Reservation Layer** via distributed locks preventing concurrent signal races from breaching limits.
- *0-0.5:* Consecutive API errors circuit breaker, Min R:R filter, Global Market Regime verification.
- *1-2:* Daily Loss Circuit Breaker (using **Net PnL deducting estimated slippage and fees**) and Account Peak Drawdown Halt.
- *3-4:* Cooldown Filters (no back-to-back entries) and Volatility band compliance via ATR%.
- *5-7:* Position Sizer (calculating exact qty via account balance), Max Position Size cap, Max Concurrent Positions limit, Total Gross Exposure cap.
- *8-10:* Cash ADV tracking, Top-of-Book Spread threshold check, Signal Time Gate constraints.
- *11-12:* Maximum Signals per day hardstop. Correlation Filter limiting over-exposure to single sectors (e.g., IT).

### 8. AutoTrader & Execution Engine
Interfaces with Brokers (Upstox/Shoonya).
- Queries `TradingModeController` for `PAPER` vs `LIVE` execution permissions. Toggle states persist via frontend local storage.
- LIVE mode pushes orders using strict limits. Includes immediate Stop-Loss protection and Slippage Buffer calculations to minimize deviation.
- Utilizes token bucket `RateLimiter` and HMAC Byte-Array Signature verification logic to guarantee payload integrity.
- **Idempotency Locks**: Deterministic hashing via `execution_dedup:hash` strictly prevents duplicate order execution over fast network retries.
- Employs session-isolated retries and a shared API error counter to handle rate-limits gracefully.

### 9. Trade Monitor & Self-Healing Reliability Layer
Runs natively inside background workers to ensure infrastructure resilience (Chaos Tested).
- **Position Reconciliation**: Identifies mismatched mismatches between local DB positions and broker states, catching naked positions and orphaned orders.
- **Periodic Order Reconciliation**: Queries lingering PENDING trades over 60 seconds old. Handles zombie position cleanup.
- **EOD Flattening (`place_market_close_order`)**: Automatically triggers an immediate exit of operations during market wrapping times (e.g. 15:15 IST).
- **Network Recovery (`pel-recovery-worker`)**: Recovers pending entries from Redis crashes gracefully, ensuring no execution is lost.

### 10. Database Architecture & Audit
Alembic migration-supported TimescaleDB running via PostgreSQL 15.
- Central Models: `symbol`, `candle`, `signal`, `live_trade`, `paper_trade`, `daily_risk_state`.
- **Order Event Audit (`order_event`)**: Tracks the strict lifecycle (`placed`, `acknowledged`, `filled`, `partially_filled`, `cancelled`, `rejected`) in explicit JSON format for audit.
- State persists on disk preventing tracking loss during docker restart.

### 11. Worker & Streaming Architecture (Redis + Kafka)
Decoupled multi-worker environments built around `XREADGROUP` and Kafka Consumers:
- **Streams**: `market:candles` → `signals:candidate` → `signals:approved` → `signals:risk_passed` → `signals:executed`.
- **Worker Types**: `SignalEngineWorker`, `RiskEngineWorker`, `AutoTraderWorker`, `TradeMonitorWorker`, `PELRecoveryWorker`.
- **Signal Engine Sharding:** deterministic `MD5(symbol) % N` to split stream reads optimally natively across container clusters scaling infinitely.

### 12. ML Feature Store
Stores mathematical artifacts natively over time inside PostgreSQL:
- Track point-in-time `FeatureSnapshot` matrices per symbol for offline backtesting and online models.
- Maintain `FeatureSymbolStats` and `FeatureRegimeStats` to visualize deviations in liquidity and volatility metrics.

### 13. Backtest & Replay Engines
Provides determinism without execution consequence:
- **Research Backtest Engine**: Uses `DataLoader`, `ResearchStrategyRunner`, Portfolio simulation, and identical logic to the live ingestion module. Includes rigorous Transaction Cost Modeling calculating NSE brokerage + STT + SEBI + GST taxes + Slippage impact natively. Returns Calmar and Sharpe ratios natively.
- **Market Replay Engine**: Emulates full ticks down the whole pipeline mimicking live latency, used for testing changes precisely via the Tick Data Lake.

### 14. Observability Stack & Telemetry
Full metrics deployment targeting Prometheus & Grafana natively (Docker `--profile monitoring` opt-in):
- **Distributed Tracing**: End-to-end `trace_id` injection across streams (`market:candles` → `signals:risk_passed` → `signals:executed`) for complete transaction observability.
- Measures `pipeline_latency`, `worker_lag_seconds`, `execution_latency`, `broker_api_errors`, and broker reconciliation stats.

---

## 📂 Project Structure

```text
quantdss/
├── backend/            # FastAPI Python application
│   ├── app/            # Main application root
│   │   ├── api/        # REST endpoints and SSE streams
│   │   ├── core/       # Global configs, Redis clients, Streams routing
│   │   ├── engine/     # Strategies, ML Pipeline, Indicators, AutoTrader
│   │   ├── ingestion/  # WebSocket handlers, Protobuf parsers, FeedManagers
│   │   ├── ml/         # ML Filters and Feature Store
│   │   ├── research/   # Backtest & Market Replay modules
│   │   └── workers/    # Entry points for Distributed Sharded Architecture
│   ├── scripts/        # Historical data fetching, seeding
│   ├── migrations/     # Alembic schema migrations
│   └── tests/          # Replay tests, unit testing
├── frontend/           # Next.js 14 Dashboard
│   ├── app/            # App router paths (Login, Dashboard, Paper, Settings)
│   └── components/     # React presentation layer, Sidebar, Topbar
├── nginx/              # Reverse proxy routing
├── monitoring/         # Prometheus, Grafana rules and setups
├── docker-compose.yml  # Distributed container orchestration
└── .env.example        # Environment variable specs
```

---

## 🚀 Quick Start Guide

### 1. Prerequisites
- [Docker](https://docs.docker.com/get-docker/) & [Docker Compose](https://docs.docker.com/compose/install/) installed.
- Valid API Credentials for your selected broker (Upstox / Angel One).
- Telegram Bot Token (Optional but highly recommended for mobile alerts).

### 2. Configure Environment
```bash
git clone https://github.com/mohd98zaid/QuantDSS.git quantdss
cd quantdss

# Create your configuration
cp .env.example .env

# Add broker credentials, DB parameters, and API configs
nano .env
```
  
> **Security Gate:** To run Live Trading execution, you must specifically verify the `.env` variable `LIVE_TRADING_LOCK=CONFIRMED`.

### 3. Start the Stack & Initialize Base Data
Run the following commands sequentially to spin up the application and prep the database:

```bash
# 1. Bring up all central containers (Opt-out Monitoring / Kafka unless stated via profiles)
docker-compose up -d --build

# 2. Run database migrations to provision the schema
docker-compose exec backend alembic upgrade head

# 3. Seed default strategy params and system configs
docker-compose exec backend python -m scripts.seed_defaults

# 4. Download historical market data for replay testing & indicators (Run once)
docker-compose exec backend python -m scripts.download_history
```

### 4. Access Modules
- **Main Dashboard**: [http://localhost](http://localhost) (Login with seeded credentials default: admin/admin)
- **Automatic API Documentation**: [http://localhost:8000/docs](http://localhost:8000/docs)
- **Monitoring (if enabled)**: [http://localhost:3000](http://localhost:3000) (Grafana metrics)

---

## 🐳 Deployment Options
- **Monolith Mode** (`WORKER_MODE=monolith`): Runs all components (streams, strategies, HTTP, alerts) within one async FastAPI thread. Perfect for lightweight deployment (< 30 stocks).
- **Distributed Mode** (Default in `docker-compose.yml`): Spins off `CandleConsumer`, `SignalEngine` (Sharded), `RiskEngine`, and `Execution` as separated horizontal Python containers scaled over Redis mapping. Perfect for full NSE500 routing.

---

## ⚖️ License
This project is restricted to **personal use only**. It is not intended for commercial distribution, advisory services, or operation under unregistered trading firm titles. All execution carries risk; use it at your own discretion.

<br/>
<p align="center">
  Built with ❤️ by <a href="https://github.com/mohd98zaid">Mohd Zaid</a>.<br/>
  <strong>⭐ If you like this project, consider giving it a star! ⭐</strong>
</p>
