# Clarification Audit Report

This document outlines areas of the QuantDSS system where the architecture, behavior, or inter-component logic requires clarification. The goal is to identify undocumented assumptions, edge cases, and ambiguous data flows to ensure complete system predictability.

## Phase 1 — Architecture Clarification
*   **Worker Scaling & Orchestration:** In [docker-compose.yml](file:///c:/Users/Xaid/Desktop/My_project/QuantDSS/docker-compose.yml), workers are defined as discrete services (e.g., `signal-engine-worker-[0..2]`). If the system needs to scale to 10 workers due to expanding the watchlist to 5000 stocks, what is the mechanism to distribute the configuration (`SIGNAL_WORKER_TOTAL=10`) dynamically? Must all containers be shut down and restarted?
*   **Kafka vs. Redis Degradation:** The environment contains `KAFKA_ENABLED=false`. When Kafka is enabled, does Redis act as a fallback if Kafka brokers are unreachable, or does the pipeline fail completely? What dictates the choice between Kafka and Redis for a given deployment?
*   **Monolith vs. Distributed Transitions:** If a user runs `WORKER_MODE=monolith` locally but deploys `distributed` in production, are there any shared state assumptions (like in-memory caches or SQLite databases) that break?

## Phase 2 — Frontend ↔ Backend Clarification
*   **SSE Token Expiration:** The [GlobalSignalListener](file:///c:/Users/Xaid/Desktop/My_project/QuantDSS/frontend/components/GlobalSignalListener.tsx#23-140) connects to SSE using a token from `localStorage`. If the JWT token expires during an active 8-hour trading session, does the SSE stream gracefully disconnect and trigger a fresh token request, or does it silently fail?
*   **Polling vs. Push Efficiency:** The frontend relies on HTTP polling (e.g., every 5 minutes) for `getMarketStatus`. Why isn't market status broadcasted over the existing Redis Pub/Sub → SSE connection to guarantee immediate status updates to all connected clients without overwhelming the backend with polling requests?
*   **Alert History Saturation:** The `quantdss_alerts` array in `localStorage` is capped at 200 items. If the user desires to audit the exact signals generated over the entire week, does the frontend have a REST endpoint to pull historical signals from the database (`signals` table), or is `localStorage` the only UI view of past alerts?

## Phase 3 — Message Pipeline Clarification
*   **Pending Entries List (PEL) Resolution:** How exactly does the system detect and handle messages stuck in the PEL? Does `pel_recovery_worker` use periodic scanning with `XPENDING` / `XCLAIM`, or does relying exclusively on consumer groups handle this natively? What is the defined timeout before a message is considered "abandoned" by a crashed worker?
*   **Dead Letter Queue (DLQ):** If a candidate signal contains malformed data causing the `risk_engine_worker` to crash repeatedly upon processing via `XCLAIM`, will it infinitely loop and block the stream? Is there a DLQ implementation to sideline poison-pill messages after $N$ failed attempts?
*   **Idempotency & Replay:** In the event of a system-wide Redis crash with AOF enabled, when the streams load back into memory, how do the workers differentiate between signals already executed vs. unexecuted signals? What is the exact TTL on the `Idempotency-Key` stored in Redis?

## Phase 4 — Signal Engine Clarification
*   **Layer Failures:** The intelligence pipeline contains 11 layers (e.g., ML, NLP, Time, Liquidity). Are the ML/NLP filters mandatory? What happens if the `ml_filter_layer` API times out? Does the pipeline fail closed (reject signal) or fail open (pass signal without ML score)?
*   **Strategy Conflicts:** During signal consolidation, if two different strategies on the same symbol generate conflicting signals (e.g., EMA Crossover says BUY, RSI Mean Reversion says SELL at the exact same minute), how does the `meta_strategy_engine` break the tie? Are both discarded, or does one strategy have a higher weighted priority?
*   **Quality Thresholds:** What is the hard numerical threshold for rejection vs. approval at the `quality_score_layer`, and is this threshold static or dynamically adjusted based on the VIX or `market_regime_filter`?

## Phase 5 — Risk Engine Clarification
*   **Locking Timeouts:** [RiskEngineWorker](file:///c:/Users/Xaid/Desktop/My_project/QuantDSS/backend/app/workers/risk_engine_worker.py#42-303) uses a Redis distributed lock for evaluating portfolio risk mathematically. If the worker's OS process is unexpectedly `SIGKILL`'d while holding this lock, what is the lock TTL? Does it prevent all other signals from being evaluated for that duration?
*   **Orphaned Reservations:** If a signal passes risk evaluation, capital is "reserved." What occurs if the signal is successfully pushed to `signals:risk_passed`, but the `autotrader-worker` crashes before processing it? How, and when, is the orphaned risk reservation released back to the daily limits?
*   **Daily State Resets:** Are the daily limits (e.g., Max Loss INR, Max Trades) reset via an external cron job at midnight IST, or is it computed functionally in Python by grouping trades in the DB by `trade_date(now)`?

## Phase 6 — Execution Engine Clarification
*   **Partial Fills on SL/Target:** [ExecutionManager](file:///c:/Users/Xaid/Desktop/My_project/QuantDSS/backend/app/engine/execution_manager.py#73-1077) immediately places conditional SL/Target legs after placing the primary LIMIT order. If the LIMIT order is only *partially filled* (e.g., 50 out of 100 shares), do the SL/Target orders automatically adjust their sizes to match the filled 50, or do they remain at size 100, risking an unintended short position?
*   **Webhook vs. Monitor Race Conditions:** What mechanism prevents a race condition at exactly 15:15 IST where the [TradeMonitorWorker](file:///c:/Users/Xaid/Desktop/My_project/QuantDSS/backend/app/workers/trade_monitor_worker.py#39-378) attempts to square off an open position just as the Upstox API sends a webhook confirming the target price was hit? Could the system attempt to sell the same position twice?
*   **Slippage Netting:** How exactly is "estimated slippage and fees" applied to the Net PnL? Is it a flat fee assumption, a percentage of exposure, or actively queried from the broker's margin API?

## Phase 7 — Background Workers Clarification
*   **Worker Process Supervision:** Inside the Docker container, is Python executing directly as PID 1? If a worker encounters an unhandled exception (e.g., out of memory from a large Pandas DataFrame), does the container simply exit and rely on Docker's `restart: unless-stopped`? 
*   **Compute Offloading:** The [worker_pool](file:///c:/Users/Xaid/Desktop/My_project/QuantDSS/backend/app/core/worker_pool.py#35-43) is used to offload TA-Lib indicator calculations. If the system is flooded with candles and the ProcessPoolExecutor queues fill up, does `await loop.run_in_executor` start blocking the asyncio event loop, causing SSE to disconnect?
*   **Scanner Worker Scheduling:** The [ScannerWorker](file:///c:/Users/Xaid/Desktop/My_project/QuantDSS/backend/app/workers/scanner_worker.py#28-118) uses `asyncio.sleep(interval)` between loops. If a scan takes 2 minutes and the interval is 5 minutes, does the next scan happen 5 minutes after the *start* or the *end* of the previous scan? 

## Phase 8 — Database Clarification
*   **TimescaleDB Retention:** 1-minute candle data streaming for hundreds of symbols will rapidly consume disk space. Is there an active continuous aggregate or data retention policy (e.g., `drop_chunks` > 30 days) configured in TimescaleDB, or will the DB grow unbounded?
*   **Live vs. Paper Trade Archiving:** Are executed [live_trades](file:///c:/Users/Xaid/Desktop/My_project/QuantDSS/backend/app/workers/trade_monitor_worker.py#200-292) and [paper_trades](file:///c:/Users/Xaid/Desktop/My_project/QuantDSS/backend/app/workers/trade_monitor_worker.py#101-197) archived into cold storage after a specific timeframe (e.g., end of fiscal year), or are queries simply expected to filter by date?

## Phase 9 — Frontend Real-Time Flow Clarification
*   **SSE Reconnection Backoff:** If the SSE connection drops, does [GlobalSignalListener](file:///c:/Users/Xaid/Desktop/My_project/QuantDSS/frontend/components/GlobalSignalListener.tsx#23-140) attempt to reconnect automatically with exponential backoff? Currently, standard `EventSource` handles reconnection natively, but does it refresh the `token` parameter if it has expired?
*   **Missed Signal Backfill:** If a user's laptop goes to sleep for 20 minutes, their SSE connection drops. Upon waking, do they receive the signals that fired during those 20 minutes to populate their toasts and history, or are those signals completely invisible to the UI?

## Phase 10 — System Operation Clarification
*   **Redis LRU Policies:** Redis is configured with `maxmemory-policy allkeys-lru`. If Redis memory hits the ceiling, `allkeys-lru` could arbitrarily evict pending Stream entries or Pub/Sub backlogs. Is there a reason `noeviction` isn't used to guarantee message durability?
*   **Credential Rotation:** If Upstox revokes the API access token mid-day, how does the user securely rotate the credentials without restarting the `autotrader-worker` docker container to reload environment variables?
*   **Log Traceability:** Without an explicit tracing library like OpenTelemetry, how does an administrator correlate a single signal's lifecycle (from Kafka/Redis → Pool → Risk → Broker → Webhook) across 5 different container logs? Is there a shared `signal_id` pushed injected into all logger contexts?
