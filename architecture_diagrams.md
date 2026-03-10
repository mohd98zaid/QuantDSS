# QuantDSS Architecture Documentation

This document provides a comprehensive visual and technical overview of the QuantDSS system architecture, based on the actual repository implementation. It covers the high-level architecture, individual pipelines, asynchronous workflows, and data flows.

---

## 1. High Level System Architecture

The QuantDSS platform uses a distributed, event-driven architecture with FastAPI serving the backend, Next.js for the frontend, and standalone Python workers processing streams of market data via Redis.

```mermaid
graph TD
    UI[Next.js Frontend Dashboard]
    API[FastAPI Backend / REST & SSE]
    RedisStream[(Redis Streams processing)]
    RedisPubSub[(Redis Pub/Sub SSE)]
    DB[(PostgreSQL Database)]
    Workers[Distributed Workers]
    Broker[Broker APIs Upstox / AngelOne]
    
    UI <-->|REST API| API
    UI <-->|SSE Stream| API
    API -->|Reads / Writes| DB
    API -->|Publishes| RedisPubSub
    
    Broker -->|Market Data WebSocket| API
    API -->|Produces| RedisStream
    
    RedisStream -->|Consumes| Workers
    Workers -->|Produces downstream| RedisStream
    Workers -->|Reads / Writes| DB
    Workers -->|Live Trades / Status| Broker
    Workers -->|Alerts| RedisPubSub
```

**Component Explanation:**
- **Next.js Frontend:** Presents the dashboard, current P&L, risk state, live charts, and real-time signal feed.
- **FastAPI Backend:** Handles HTTP requests, orchestrates market data ingestion, manages database tables via SQLAlchemy, and serves the SSE real-time stream.
- **Redis Streams:** The backbone of the asynchronous message pipeline (`market:candles`, `signals:approved`, etc.).
- **Distributed Workers:** Independent Python processes (`signal_engine_worker`, `risk_engine_worker`, `autotrader_worker`, `trade_monitor_worker`) that distribute the computational load.
- **PostgreSQL Database:** The persistent source of truth containing configs, trade journals, paper trades, and daily risk states.
- **Broker APIs:** Upstox and AngelOne integration for market ticks, order placement, and webhooks.

---

## 2. Market Data Pipeline

Market data is ingested from the broker, converted to candles, and dispatched for strategy evaluation. 

```mermaid
graph TD
    BrokerWS[Broker WebSocket Feed]
    MarketDataCache[Market Data In-Memory Cache]
    CandleAggregator[Candle Aggregation Layer]
    RedisCandles[(Redis Stream: market:candles)]
    SignalWorker[Signal Engine Worker]
    TradeMonitor[Trade Monitor Worker]
    
    BrokerWS --> MarketDataCache
    BrokerWS --> CandleAggregator
    MarketDataCache --> TradeMonitor
    CandleAggregator --> RedisCandles
    RedisCandles --> SignalWorker
```

**Explanation:**
- Raw ticks arrive via WebSocket and are instantly cached in `MarketDataCache` (used by [ExecutionManager](file:///c:/Users/Xaid/Desktop/My_project/QuantDSS/backend/app/engine/execution_manager.py#73-1077) for slippage/drift checks and [TradeMonitorWorker](file:///c:/Users/Xaid/Desktop/My_project/QuantDSS/backend/app/workers/trade_monitor_worker.py#39-378) for open position monitoring).
- `CandleAggregator` batches ticks into time-bucketed candles (e.g., 5-minute intervals).
- Candles are sent to the `market:candles` stream. If running in monolithic mode, an in-process `CandleConsumer` evaluates them; otherwise, the distributed [SignalEngineWorker](file:///c:/Users/Xaid/Desktop/My_project/QuantDSS/backend/app/workers/signal_engine_worker.py#36-309) pulls from the stream.

---

## 3. Signal Engine Pipeline

The signal intelligence pipeline evaluates trading strategies and refines signals through a strict 11-layer gauntlet.

```mermaid
graph TD
    Candles[(Redis Stream: market:candles)]
    Scanner[(Redis Stream: signals:candidate)]
    Strat[Strategy Runner Evaluation]
    Pool[Candidate Signal Pool]
    Consolidation[Consolidation Layer]
    Meta[Meta Strategy Engine]
    Confirm[Confirmation Layer]
    Quality[Quality Score Layer]
    Regime[Market Regime Filter]
    ML[Machine Learning Filter]
    NLP[NLP Sentiment Filter]
    Time[Time Filter]
    Liquidity[Liquidity Filter Layer]
    TerminalPub[Publish to signals:approved]

    Candles --> Strat
    Strat --> |Signal Deduplication| Pool
    Scanner --> Pool
    Pool --> Consolidation
    Consolidation --> Meta
    Meta --> Confirm
    Confirm --> Quality
    Quality --> Regime
    Regime --> ML
    ML --> NLP
    NLP --> Time
    Time --> Liquidity
    Liquidity --> TerminalPub
```

**Explanation:**
- **Strategy Runner:** Applies 9+ technical strategies (EMA Crossover, RSI Mean Reversion, VWAP Reclaim, etc.) to incoming candles.
- **Signal Deduplication:** Prevents identical signals from flooding the system within a small window.
- **Pipeline Gauntlet:** Signals pass sequentially through various layers that consolidate related signals, apply meta-rules, score quality based on ATR/volume, determine market regime, evaluate ML/NLP probabilities, and filter out low-liquidity/wrong-time trades.
- At the end of the chain, surviving signals are published to the `signals:approved` stream.

*(Note: Depending on distributed vs. monolithic mode, [signal_engine_worker.py](file:///c:/Users/Xaid/Desktop/My_project/QuantDSS/backend/app/workers/signal_engine_worker.py) dynamically wires these callbacks together.)*

---

## 4. Risk Engine Flow

The comprehensive risk validation process prevents excessive exposure and enforces daily loss limits.

```mermaid
graph TD
    SigApprov[(Redis: signals:approved)]
    RiskWorker[Risk Engine Worker]
    Lock[Distributed Lock 'risk_eval:portfolio']
    LoadState[Load Daily State & Portfolio]
    RiskVal[Risk Rule validations]
    RiskReserve[Atomic Risk Reservation]
    FailDB[(DB: Rejected Signal)]
    PassQ[(Redis: signals:risk_passed)]

    SigApprov --> RiskWorker
    RiskWorker --> Lock
    Lock --> LoadState
    LoadState --> RiskVal
    
    RiskVal -->|Reject: Max Exposure, Loss Limit, etc.| FailDB
    RiskVal -->|Approve| RiskReserve
    RiskReserve --> PassQ
```

**Explanation:**
- The [RiskEngineWorker](file:///c:/Users/Xaid/Desktop/My_project/QuantDSS/backend/app/workers/risk_engine_worker.py#42-303) consumes the approved signals.
- It acquires a distributed Redis lock (`risk_eval:portfolio`) to prevent race conditions during risk portfolio evaluation.
- It loads the `DailyRiskState` (today's P&L, blocked signals count) and `Portfolio` (virtual or live balance, open positions).
- Various checks are applied: **Stale signal check (max 300s age)**, **Max Weekly/Daily Loss limits**, **Max Position Pct**, **Correlated positions**, and **Cooldown periods**.
- If approved, risk is atomically reserved in Redis (to prevent concurrent trade over-allocation) and the signal flies to `signals:risk_passed`.

---

## 5. Execution Pipeline

Translates approved and risk-verified signals into physical broker orders or simulated paper trades.

```mermaid
graph TD
    RiskPass[(Redis: signals:risk_passed)]
    AutoTrader[AutoTrader Worker]
    ConfigCheck[AutoTradeConfig Check]
    ExeMgr[Execution Manager]
    Upstox[Upstox API]
    PaperTradeDB[(DB: PaperTrade)]
    LiveTradeDB[(DB: LiveTrade)]
    ExecutedQ[(Redis: signals:executed)]
    
    RiskPass --> AutoTrader
    AutoTrader -->|Stale Check < 60s & Hours Check| ConfigCheck
    
    ConfigCheck -->|Paper Mode| PaperTradeDB
    ConfigCheck -->|Live Mode| ExeMgr
    
    ExeMgr -->|Slippage & Drift Check| Upstox
    Upstox -->|HTTP 200| LiveTradeDB
    
    PaperTradeDB --> ExecutedQ
    LiveTradeDB --> ExecutedQ
```

**Explanation:**
- [AutoTraderWorker](file:///c:/Users/Xaid/Desktop/My_project/QuantDSS/backend/app/workers/autotrader_worker.py#45-331) pulls from `signals:risk_passed`. It uses an idempotency key to prevent double execution.
- Depending on the database `AutoTradeConfig` (Paper or Live mode), it splits logic.
- **Paper Mode:** Simulates order execution against virtual balance and logs to `PaperTrades`.
- **Live Mode:** Dispatches to [ExecutionManager](file:///c:/Users/Xaid/Desktop/My_project/QuantDSS/backend/app/engine/execution_manager.py#73-1077). It checks LTP drift (to prevent execution on stale signal price) and submits an Intraday LIMIT order to Upstox.
- Both modes publish a final receipt to `signals:executed`.

---

## 6. Message Queue Architecture

An overview of the Redis Pub/Sub and Streams topography connecting the decoupled services.

```mermaid
graph TD
    marketCandles[(Stream: market:candles)]
    signalCandidate[(Stream: signals:candidate)]
    signalApprov[(Stream: signals:approved)]
    signalRisk[(Stream: signals:risk_passed)]
    signalExec[(Stream: signals:executed)]
    pubsubSSE(((Pub/Sub: sse:signals)))

    Aggregator -.->|Publish| marketCandles
    Scanner -.->|Publish| signalCandidate
    
    marketCandles -.->|Consume| SignalWorker
    signalCandidate -.->|Consume| SignalWorker
    SignalWorker -.->|Publish| signalApprov
    SignalWorker -.->|Publish| pubsubSSE
    
    signalApprov -.->|Consume| RiskWorker
    RiskWorker -.->|Publish| signalRisk
    
    signalRisk -.->|Consume| AutoTraderWorker
    AutoTraderWorker -.->|Publish| signalExec
    
    BackendSSE -.->|Subscribe| pubsubSSE
```

**Explanation:**
- Redis Streams acts as a durable log for the primary event lifecycle (Ingestion → Intelligence → Risk → Execution).
- Redis Pub/Sub (`sse:signals`) is used exclusively for ephemeral, low-latency UI updates (Server-Sent Events) so the dashboard sees the signal instantly without polling.

---

## 7. Worker Architecture

The background processing ecosystem handles asynchronous tasks independently from the UI.

```mermaid
graph TD
    Signal[Signal Engine Worker]
    Risk[Risk Engine Worker]
    Trader[AutoTrader Worker]
    Monitor[Trade Monitor Worker]
    Recovery[PEL/Position Recovery Workers]

    Candles[(Candles)] --> Signal
    Signal -->|Pipeline output| Risk
    Risk -->|Authorized| Trader
    Trader -->|DB state open| Monitor
    
    Monitor -->|EOD or Trailing Stops| BrokerAPI
    Recovery -->|Reconciliation| DB[(Database)]
```

**Explanation:**
- **Signal Engine Worker:** Heavy compute. Evaluates DataFrame strategies. 
- **Risk Engine Worker:** Stateful logic. Manages lock contention and portfolio exposure limits.
- **AutoTrader Worker:** Connects to broker. Manages live execution state and errors.
- **Trade Monitor Worker:** A polling loop (every 15s) checking open trades. It calculates Trailing Stops, detects SL hits locally (fallback), and manages EOD 15:15 market close logic.
- **Position Reconcilers & Recovery Workers:** Run asynchronously or at startup to reconcile pending orders and recover from crashes.

---

## 8. Frontend Dashboard Flow

How the UI interacts with the backend to stay entirely responsive without pageloads.

```mermaid
graph TD
    User((User))
    UI[Next.js App / page.tsx]
    FastAPI[FastAPI Router]
    DB[(PostgreSQL)]
    SSEManager[SSE Manager]
    RedisBus(((Redis Pub/Sub)))

    User -->|Views / Clicks| UI
    UI -->|REST GET /api/v1/health| FastAPI
    UI -->|REST GET /api/v1/risk/state| FastAPI
    UI -->|REST GET /api/v1/candles| FastAPI
    
    FastAPI --> DB
    
    UI -->|EventSource Connection| FastAPI
    FastAPI -->|Yield stream| SSEManager
    SSEManager -->|Subscribe| RedisBus
```

**Explanation:**
- On initial load, the dashboard fetches static KPIs (Today's PNL, Open Positions, System Health) via standard REST API calls to the FastAPI backend.
- It establishes a continuous connection via `EventSource` to the SSE endpoint.
- As workers perform actions and publish to `sse:signals`, the SSE manager loops those messages directly back out to the React components (`setSignals`), rendering UI feed updates in real-time.

---

## 9. Complete End-to-End System Flow

The complete lifecycle from the moment a stock's price changes at the broker to the moment the dashboard updates.

```mermaid
graph TD
    BrokerWS[Upstox/Angel WS] -->|Ticks| Aggregator[Candle Aggregation]
    Aggregator -->|Candle| CandStream[(market:candles)]
    CandStream --> SignalEng[Signal Engine Worker]
    SignalEng -->|Evaluates Strategies| Intelligence[11-Layer Pipeline]
    Intelligence -->|Filtered| ApprStream[(signals:approved)]
    
    ApprStream --> RiskEng[Risk Engine Worker]
    RiskEng -->|Portfolio Checks & Lock| RiskStream[(signals:risk_passed)]
    
    RiskStream --> ExecEng[AutoTrader Worker]
    ExecEng -->|Paper / Live Check| ExecMgr[Execution Manager]
    ExecMgr -->|Slippage Check| BrokerAPI[Upstox REST API]
    BrokerAPI -->|Order ID| DB[(PostgreSQL)]
    
    BrokerAPI -->|Webhook Update| WebhookHandler[Webhook Endpoint]
    WebhookHandler --> DB
    
    DB --> TradeMon[Trade Monitor Worker]
    TradeMon -->|Calculates Trailing Stop| BrokerAPI
    
    Intelligence -.->|SSE Trigger| RedisPubSub(((Redis Pub/Sub)))
    RiskEng -.->|SSE Trigger| RedisPubSub
    RedisPubSub -.->|SSE Streams| Dashboard[Next.js UI]
    DB -.->|Initial KPIs| Dashboard
```

**Key Takeaways:**
- **Decoupled:** Each phase operates on its own loop/stream. A crash in execution does not halt data ingestion or signal generation.
- **Stateful Safety:** Trade state is tracked precisely in the PostgreSQL [live_trades](file:///c:/Users/Xaid/Desktop/My_project/QuantDSS/backend/app/workers/trade_monitor_worker.py#200-292) table. Webhooks and secondary polling (`Trade Monitor`) ensure no trade is left 'naked'.
- **Transparent:** The [SSEManager](file:///c:/Users/Xaid/Desktop/My_project/QuantDSS/backend/app/alerts/sse_manager.py#14-95) ensures every worker can inform the frontend effortlessly via Redis Pub/Sub, delivering a premium user experience on the React dashboard.
