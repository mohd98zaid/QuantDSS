# QuantDSS System Documentation Report

> **Quantitative Decision Support & Execution System for Intraday Indian Equity Markets**
> A comprehensive architectural and operational deep-dive of the QuantDSS system.

---

## SECTION 1 — PROJECT STRUCTURE

The QuantDSS repository is structured recursively combining a FastAPI backend, background workers, PostgreSQL DB, Redis cache/streams, and an SSE-based UI.

### Directory Tree & Modules
- **`backend/app/`**: Core monolithic codebase.
  - **`api/`**: FastAPI routers (`main.py`, `routers/autotrader.py`, `scanner.py`, `risk.py`, etc.).
  - **`core/`**: Configuration (`config.py`), logging, Redis client (`redis.py`), DB sessions, centralized streams defs (`streams.py`).
  - **`engine/`**: The brain. Contains Strategy logic (`strategies/`), Indicator engine, Signal Intelligence Pipeline (11 layers), Risk Engine, Execution Manager, Multi-Timeframe Engine, and AutoTrader Engine.
  - **`ingestion/`**: Market data connectors. Upstox HTTP client, WebSocket Manager, Protobuf parsers, and Volume Delta trackers.
  - **`models/`**: SQLAlchemy ORM models mapping to the database (Signals, Trades, Health, Config).
  - **`workers/`**: Entry points for the distributed background architecture (`signal_engine_worker.py`, `risk_engine_worker.py`, etc.).
  - **`alerts/`**: Server-Sent Events (SSE) manager pushing live updates to the frontend UI.
- **`backend/tests/`**: Unit and integration test suite.
- **`docker-compose.yml`**: Outlines the multi-container deployment architecture.

### Core System vs Worker Modules
- **Core System Modules**: `engine`, `models`, `core`.
- **Data Ingestion Modules**: `ingestion`, feeding Redis `market:candles`.
- **Strategy Modules**: `engine/strategies`.
- **Execution Modules**: `engine/execution_manager.py`, `engine/auto_trader_engine.py`.
- **Worker Modules**: `workers/*` (Run completely decoupled or inside a monolith).

---

## SECTION 2 — SYSTEM ARCHITECTURE

QuantDSS processes live broker tick data, converts it to candles, evaluates quantitative strategies, filters signals through a strictly enforced 11-layer Intelligence Pipeline, subjects them to 17 Risk Rules, and executes via broker APIs.

### Architecture Modes
1. **Monolithic Mode (`WORKER_MODE=monolith`)**: The FastAPI server loads the ingestion loop, pipeline, risk engine, and execution loops into background `asyncio` tasks inside a single process.
2. **Distributed Worker Architecture**: Used in production (via `docker-compose.yml`), where processes are decoupled over Redis Streams:
   - **API Server** (`backend`): Handles HTTP requests, SSE streaming, and REST commands.
   - **Signal Engine Worker**: Consumes `market:candles`, runs strategies & the 11-layer pipeline, publishes to `signals:candidate` and `signals:approved`.
   - **Risk Engine Worker**: Consumes `signals:approved`, applies the 17-rule Risk Engine, publishes to `signals:risk_passed`.
   - **AutoTrader Worker**: Consumes `signals:risk_passed`, calls Execution Manager to place entry orders via broker APIs, publishes to `signals:executed`.
   - **Trade Monitor Worker**: Manages SL/TP logic, webhook reconciliations, end-of-day squared-off logic.
   - **Redis**: The message backbone linking the workers.
   - **TimescaleDB / PostgreSQL**: Persistent storage.

---

## SECTION 3 — DATA INGESTION LAYER

Market data enters primarily through the **Upstox WebSocket Full-C stream**. 

1. **Websocket Ingestion (`websocket_manager.py`)**: Subscribes to configured instrument tokens. Parses Protobuf binary `MarketDataFeed`. 
2. **Tick Normalization & Volume**: Uses a `VolumeDeltaTracker` to convert the cumulative daily volume (VTT) received from Upstox into isolated per-tick delta volumes, preventing phantom volume spikes that corrupt VWAP and liquidity filters.
3. **Market Data Cache**: In-memory cache tracking the latest LTP, Best Bid/Ask, Delta Volume, and freshness timestamps (`MarketDataCache`).
4. **Candle Aggregation**: Ticks are aggregated into 1-minute OHLCV candles (`CandleAggregator`) and immediately published to the `market:candles` Redis Stream.
5. **Gap Recovery**: If the WebSocket drops, upon reconnect, the system queries the Upstox REST API for 1-minute historical intraday candles to backfill the gap, pushing them to Redis so strategies don't miss indicators.

---

## SECTION 4 — MULTI-TIMEFRAME ENGINE

Driven by `engine/mtf_engine.py`, the system builds hierarchical timeframes:
- **`1s`**: Formed directly from live ticks.
- **`5s`**: Aggregated from 5 completed `1s` candles.
- **`15s`**: Aggregated from 3 completed `5s` candles.
- **`30s`**: Aggregated from 2 completed `15s` candles.
- **`1m`**: Formed primarily via `CandleAggregator`, but the MTF engine maps it upwards.
- **`5m`**: Aggregated from 5 completed `1m` candles.

Strategies request exact timeframes. E.g., a strategy running on 5m candles automatically evaluates only when a 5m candle closes.

---

## SECTION 5 — INDICATOR ENGINE

Located in `engine/indicators.py`, running on pandas using the `ta` library for zero C-binding overhead (< 50ms compute).
- **EMA/SMA**: Moving averages for trend.
- **RSI**: Relative Strength Index.
- **ATR**: Average True Range for volatility measuring and dynamic Stop-Loss assignment.
- **MACD**: Histogram and Signal lines used as primary confirmations.
- **Volume MA**: SMA of relative volume to catch expansions.
- **VWAP**: Session-anchored Volume Weighted Average Price. Resets cleanly at session start via a pandas `.dt.date` grouper to prevent multi-day bleeding.
- **Bollinger Bands**: Upper, Middle, Lower bands.
- **ORB (Opening Range Breakout) High/Low**: Snaps the highest-high and lowest-low of the first 15 minutes of the trading day.

---

## SECTION 6 — STRATEGY ENGINE

Located in `engine/strategies/`. Inherited from `BaseStrategy`. They output strictly immutable `CandidateSignal` objects.

1. **EMA Crossover**: Fast EMA crossing Slow EMA + MACD confirmation.
2. **RSI Mean Reversion**: Looks for oversold/overbought extremes against a 50 EMA baseline.
3. **ORB+VWAP**: Breakout above/below ORB 15-min lines combined with VWAP positioning.
4. **Volume Expansion**: Spikes in volume > Volume MA combined with ATR expansion.
5. **Trend Continuation**: Pullbacks to 9 EMA / 21 EMA in the direction of the 50 EMA.
6. **Failed Breakout**: Fading false breakouts through VWAP utilizing RSI divergence.
7. **VWAP Reclaim**: Stock trades below VWAP, then reclaims it on high volume.
8. **Trend Pullback**: RSI pullback (40-60) into the 21 EMA during an uptrend.
9. **Relative Strength**: Outperformance against the Nifty 50 benchmark over a 1-hour lookback.

---

## SECTION 7 — META STRATEGY ENGINE

Located in `meta_strategy_engine.py`. Sitting high up the Intelligence Pipeline (Layer 4).
- **Purpose**: Controls strategy execution gating dynamically.
- **Health Monitoring**: Ties into `strategy_health.py`, tracking 7-day trailing win-rates per strategy. If a strategy's win-rate drops below a predefined threshold (e.g., 35%), the Meta Strategy Engine natively strips out its signals.
- **Regime Gating**: Blocks strategy output if the strategy doesn't map to the current global market regime (e.g., blocks "RSI Mean Reversion" entirely when the master regime is flagged as "TRENDING").

---

## SECTION 8 — SIGNAL INTELLIGENCE PIPELINE

A strict 11-layer gauntlet through which every signal must pass. Located across `engine/*_layer.py`.

1. **Signal Deduplication**: Prevents identical signals (same strategy/symbol/direction) within a 15-minute TTL.
2. **Signal Pool**: Buffers concurrent signals.
3. **Consolidation**: Merges simultaneous signals on the same symbol from different strategies and resolves conflict directions (Long vs Short collisions).
4. **Meta-Strategy Engine**: Blocks poorly performing or out-of-regime strategies.
5. **Confirmation Layer**: Wait for multi-strategy alignment before passing. 
6. **Quality Score**: Assigns a 0-100 score based on Trend, VWAP position, Volume divergence, and Spread. High/Medium tier classification.
7. **Market Regime Filter**: Re-evaluates individual symbol regime (Trend/Range/High Volatility/Low Liquidity).
8. **ML Filter (Shadow)**: Queries isolation forests to output probabilistic win likelihood.
9. **NLP Filter (Shadow)**: Evaluates breaking news sentiment on the symbol.
10. **Time Filter**: Enforces hard trading boundaries (e.g., skips signals arriving outside 09:20–14:30 IST).
11. **Liquidity Filter**: Minimum Average Daily Volume (ADV) check and strict bid/ask spread tolerance.

---

## SECTION 9 — SIGNAL FLOW

Lifecycle Traceability from Candle to Trade:

1. **Candle Check**: `CandleConsumer` spots a new 1m candle on Redis `market:candles`.
2. **Strategy Evaluation**: Sends dataframe to `StrategyRunner`. Strategies return `CandidateSignal` arrays.
3. **Intelligence Pipeline**: Sent deep into the 11 layers defined in Section 8. Converts into `ConsolidatedSignal`.
4. **Final Alert Generator**: Terminal layer of the intelligence pipeline. Calls the Risk Engine.
5. **Risk Engine Validation**: Signal scrutinized against 17 rigid risk checks.
6. **Persistence & UI**: If APPROVED, written to PostgreSQL Signals table and broadcasted via SSE to frontend.
7. **AutoTrader Queue**: Signal pushed to `AutoTraderEngine` (via Redis `signals:risk_passed` or immediately enqueued depending on architecture).
8. **Execution**: `ExecutionManager` issues the LIMIT order to the broker.

---

## SECTION 10 — RISK ENGINE

A strictly enforced sequence of 17 rules in `engine/risk_engine.py` using fail-fast execution.

0. **Consecutive Errors Circuit Breaker**: Disables trading upon consecutive broker API faults.
0.1. **Min Risk-Reward Filter**: Blocks if SL/TP distance provides an R:R below config (e.g., < 1.5).
0.5. **Global Market Regime**: Disables strategies unfitting for the day's macro topology.
1. **Daily Loss Circuit Breaker**: Hard halt if realized DB PnL descends below the absolute INR / percentage limit.
1b. **Weekly Loss Circuit Breaker**: New safety hook blocking rapid daily cap burn.
2. **Account Drawdown Halt**: Stops operations if account value shrinks by X% from peak high water mark.
3. **Cooldown Filter**: Disables back-to-back entries on the same symbol inside X minutes.
4. **Volatility Filter**: Checks if ATR% is within allowed bands.
5. **Position Sizer (Risk Budgeting)**: Determines share quantity securely using Account Balance × Allowed Risk%. Subtracts explicitly *committed_risk* from active open positions to prevent risk blowups.
6. **Max Position Size Cap**: Hard ceiling enforcing no single position exceeds X% of total portfolio value.
7. **Max Concurrent Positions**: Skips if portfolio already has N open trades.
7b. **Gross Exposure Filter**: Hard skips if total aggregated notional value of all open positions breaches 40% of the account.
8. **Liquidity Filter**: Checks symbol's cached ADV (Average Daily Volume) from DB.
9. **Spread Filter**: Requires Upstox real-time Bid/Ask spread % to be tight.
10. **Signal Time Gate**: Intraday start/cutoff hour enforcement.
11. **Max Signals Per Stock**: Limits over-trading (e.g., max 3 signals per stock per day), state persists in DB.
12. **Correlation Filter**: Sectors are mapped (e.g. IT, BANKING). Limits max concurrent positions occurring within the identically correlated sector.

---

## SECTION 11 — EXECUTION ENGINE

The operational interface with the Broker (`engine/execution_manager.py`).

1. **Broker Integration**: Native Upstox HTTP API (via `UpstoxHTTPClient`) using token refresh loops. Token payload is dynamically verified against app configurations.
2. **Order Placement**: Takes entry prices from signal and pads via Slippage Buffer. Generates an Upstox `LIMIT` order instead of Market to cap entry deviation.
3. **Webhook Reconciliation**: Upstox webhooks notify the endpoint. Validates using a strict Byte-Array HMAC to prevent deserialization signature failures.
4. **Automated SL & TP**: Uses post-fill event chaining. `place_sl_order()` (an SL-M Market Stop Loss) and `place_target_order()` (LIMIT order) are fired the moment the `handle_webhook` declares the parent entry as OPEN.
5. **Partial Fills**: Webhook natively recalculates `risk_amount` and shrinks `stop_loss` exposure dynamically, replacing the old Target order with the new shrunken partial quantity.
6. **Retries & Rate Limits**: Uses an Async Token Bucket `RateLimiter` (5 calls/s default) + Exponential Backoff `_retry_api_call`.

---

## SECTION 12 — TRADE MONITOR

Managed primarily by `app.workers.trade_monitor_worker`:
- **Periodic Order Reconciliation**: In case of lost Webhooks, queries Upstox `/v2/order/details` for all PENDING trades older than 60s.
- **Stale Pending Order Cleanup**: Cancels PENDING orders that haven't triggered their Limit entry successfully within 5 minutes.
- **EOD Flattening**: Executes `place_market_close_order()` to execute market exits across all OPEN positions when clock breaches the EOD square-off time (e.g. 15:15 IST).
- **Trailing Stops (Future/Partial implementation)**: Checks if average price moved up `X`%, moving local SL markers.

---

## SECTION 13 — DATABASE ARCHITECTURE

Designed via SQLAlchemy running TimescaleDB (PostgreSQL).

- **`symbol`**: Instrument lists, indices, exchanging data, ADV tracking.
- **`candle`**: Processed 1m historical ticks for backfill.
- **`signal`**: Terminal resting spot of `ConsolidatedSignal`. Contains score mappings (`quality_score`, `confidence_tier`), reasons for Risk Rejection, and ML Probability ratings.
- **`live_trade`**: Core live execution DB. Tracks quantities, `broker_order_id`, `sl_order_id`, `target_order_id`, `risk_amount`, Entry, Exit, and Slippage metrics.
- **`paper_trade`**: Parallel table for Simulator usage.
- **`daily_risk_state`**: Single row per day storing `realised_pnl`, `signals_per_stock` (JSON dict for restart safety), and explicit Circuit Breaker booleans (`is_halted`).
- **`risk_config` & `auto_trade_config`**: Stores the 17 risk-rule parameters and toggle execution states, allowing live modification without restarts.
- **`strategy_health`**: Stores trailing metric logs of performance vs strategy type.

---

## SECTION 14 — REDIS STREAM ARCHITECTURE

The absolute backbone of distributed state and event loops via `core/streams.py`.

- **`market:candles`**: Fast pipeline. `websocket_manager` -> `candle_aggregator` dumps finished 1m candles here. `signal_engine_worker` consumes this.
- **`signals:candidate`**: Standard output pool of individual strategies.
- **`signals:approved`**: Signals that managed to survive the 11-Layer Intelligence Pipeline. Produced by `signal_engine_worker`, consumed by `risk_engine_worker`.
- **`signals:risk_passed`**: Passed 17 risk rules. Safe for entry consideration. Consumed by `autotrader_worker`.
- **`signals:executed`**: Logged completed states for UI distribution and post-trade performance analytics.

*Note: All streams cap securely via `maxlen=10_000` to prevent memory blowouts.*

---

## SECTION 15 — WORKER ARCHITECTURE

Decoupled Python processes listening to Redis `XREADGROUP` loops.

1. **SignalEngineWorker**: Consumes candles, runs Indicators + Multi-Timeframe logic, processes Strategies, performs the 11-stage Signal Intelligence Pipeline. Emits to `signals:approved`.
2. **RiskEngineWorker**: Consumes approved signals, performs 17-Rule strict validation checking PnL caps and Portfolio size limits. Emits to `signals:risk_passed`.
3. **AutoTraderWorker**: Consumes risk-passed signals, queries `TradingModeController`, delegates command to `ExecutionManager` or `PaperMonitor`.
4. **TradeMonitorWorker**: Polls Webhooks misses, monitors open trades, cancels stale pending orders, and performs EOD flattening.

---

## SECTION 16 — API LAYER

FastAPI deployed at `backend/app/api/routers/`.

- **`auth.py`**: JWT Authentication token dispenser (`/auth/login`).
- **`scanner.py`**: Ad-hoc run of the pipeline on user demand, allowing UI to pull real-time scan metrics across sectors.
- **`auto_trader.py`**: Routes `/start`, `/stop`, `/flatten` emergency square offs, and fetches live states.
- **`health.py`**: Broker API connectivity pingers.
- **`market_data.py`**: Historical fetching wrapper.
- **`risk.py`**: Config updates endpoint, allowing hot-swapping Risk Rata (e.g., Max Exposure).
- **`trades.py`**: Returns paginated views of `LiveTrade` and `PaperTrade` models.
- **`stream.py`**: Exposes `/stream` Server-Sent Events (SSE) manager feeding the Next.js React frontend.

---

## SECTION 17 — TRADING MODE CONTROL

Managed via `engine/trading_mode.py`. Acts as the universal execution gate.

Types:
- **`DISABLED`**: Pipeline runs, but drops at `FinalAlertGenerator`.
- **`PAPER`**: Signals that pass risk are executed using simulated time-series latency inside `paper_monitor.py`. Written only to `paper_trades` table.
- **`LIVE`**: Deploys via `execution_manager.py` against actual Upstox APIs.

**Security Gating**: 
To prevent UI manipulation, **LIVE** execution demands a double-lock:
1. `AutoTradeConfig.mode == "live"` in the Database.
2. The Environmental Variable `LIVE_TRADING_LOCK=CONFIRMED` natively deployed on the runtime image. 

---

## SECTION 18 — SIGNAL TRACEABILITY

Managed heavily through `engine/signal_trace.py`.
- **`trace_id`**: A 12-char UUID attached immediately the moment a signal is triggered.
- Flows seamlessly through logs formatted as `[TRACE:xxx] STAGE | SYMBOL | DROPPED_OR_PASSED`. 
- Allows instantaneous debug tracking from `[TRACE:12ab34] CANDLE_CONSUMER` down to `[TRACE:12ab34] TRADE_EXECUTION`.

---

## SECTION 19 — DEPLOYMENT ARCHITECTURE

Deployed via `docker-compose.yml` into a localized or cloud-hostable ecosystem.
1. **postgres**: Using `timescale/timescaledb` for rapid time-series optimization. 
2. **redis**: `redis:7-alpine` acting as caching layer and the robust Stream PubSub.
3. **backend**: The core FastAPI API server.
4. **Workers**: Independent python processes instantiated using `Dockerfile.worker`. Defined individually in `docker-compose` (`signal-engine-worker`, `risk-engine-worker`, etc).
5. **frontend**: Separate UI container served usually at `port 3000`.
6. **nginx**: Reverse Proxy gating traffic securely, mapping `/api` to the backend, `/` to the frontend.

---

## SECTION 20 — DATA FLOW DIAGRAM

### Market Data Flow
`[Broker Websocket]` **→** `[Tick Normalizer & Volume Delta]` **→** `[Candle Aggregator]` **→** `(Redis: market:candles)`

### Analytical Signal Flow
`(Redis: market:candles)` **→** `[Indicator/MTF Engines]` **→** `[Strategy Evaluation]` **→** `[Intelligence Pipeline (Layers 1-11)]` **→** `[Final Alert Generator]`

### Execution Trade Flow
`(Final Alert)` **→** `[Risk Engine (17 Rules)]` **→** `(Redis: signals:risk_passed)` **→** `[Trading Mode Controller]` **→** `[AutoTrader Worker]` **→** `[Execution Manager]` **→** `[Broker API (LIMIT ENTRY)]` **→** `[Partial/Complete Webhooks]` **→** `[Place SL-M & Target Orders]`

---

## SECTION 21 — FAILURE HANDLING

- **Websocket Drop**: Supervisor auto-restarts the stream; executes Gap Recovery by calling REST intraday fetching, publishing retroactively to Redis Stream so Indicators do not falter.
- **Redis Outages**: Workers utilize exception catching and exponential backoffs automatically re-attempting `XREADGROUP`.
- **Broker Rate-Limits / API Reject**: Caught by `ConsecutiveErrorsCircuitBreaker`. Hard-halts operations if 5 consecutive API errors arise, reverting to manual recovery.
- **Missed SL Fills / Disconnects**: `TradeMonitorWorker` sweeps the broker states natively every 60 seconds reconciling `PENDING` versus `OPEN` trades to eliminate localized ghost positions.

---

## SECTION 22 — PERFORMANCE DESIGN

- **Async Architecture**: Utilizing `asyncio` loop handling, DB connections (`asyncpg`), and async `httpx` logic drastically eliminates request locking.
- **Worker Concurrency**: Separation using Redis PubSub streams prevents high-CPU strategy evaluation bounds from blocking the API event loop and WebSockets.
- **Zero-C Pandas processing**: Indicator engine relies upon direct `ta` implementation optimizing dataframe loops to under 50ms compute times for heavy data blocks.
- **Volume Calculation O(1)**: `VolumeDeltaTracker` limits complex iterative summative calls.

Potential Bottlenecks: Uncapped signal expansion if > 100 instruments are subscribed generating complex multi-timeframes simultaneously. The single `SignalEngineWorker` might require horizontal scaling if processing goes > 1min delay length.

---

## SECTION 23 — SECURITY MODEL

- **JWT Auth**: Single user strict control, JWT hashed through `HS256` stored securely in headers. 
- **Secret Management**: No hardcoded keys. All Upstox APIs, DB passwords, and Secret Tokens enforce presence in `.env`.
- **Broker Creds**: Token endpoints interact natively; `execution_manager` validates HMAC `x_upstox_signature` hashes strictly against the completely raw byte stream verifying Upstox payloads successfully before executing SL/TP.
- **Safe LIVE Trading Toggle**: Mandates environment variable physical override (`LIVE_TRADING_LOCK="CONFIRMED"`).

---

## SECTION 24 — SYSTEM LIMITATIONS

1. **State Loss on Complete Restart**: While DB persists long-term stats, in-memory architectures (`VolumeDeltaTracker` baselines and `MarketDataCache`) reconstruct context upon restarting, creating potentially brief blindspots.
2. **Redis Message Flooding**: Without strict `maxlen=10_000` adjustments, high volatility could trigger Redis RAM allocation scaling rapidly.
3. **Strategy Abstraction Complexity**: While robust, the 11-Layer pipeline prevents rapid, ad-hoc simple strategy deployment. A single logic change must account for deduplication pooling constraints. 
4. **Current Trailing Systems**: Complex dynamic algorithmic trailing stops are only partially mapped inside `TradeMonitorWorker` operations.
