# QuantDSS — Personal Quant Trading Decision Support System

> **QuantDSS is a discipline-enforcement tool. It does not predict the market. It enforces the rules you already know but fail to follow.**

## Overview

QuantDSS is a self-hosted, zero-cost trading decision support system for Indian NSE/BSE cash equities. It generates rule-based trade signals, enforces mandatory risk management, and provides an automated trade journal — all without automated order execution.

## Tech Stack

| Layer           | Technology                                        |
| --------------- | ------------------------------------------------- |
| Frontend        | Next.js 14 + TypeScript + shadcn/ui + TailwindCSS |
| Backend         | FastAPI (Python 3.11) + Uvicorn                   |
| Database        | PostgreSQL 15 + TimescaleDB 2.x                   |
| Cache/Queue     | Redis 7                                           |
| Broker          | Shoonya (Finvasia) — free                         |
| Historical Data | yfinance — free                                   |
| Notifications   | Telegram Bot                                      |
| Containers      | Docker Compose                                    |

## Quick Start

```bash
# 1. Clone and configure
git clone <your-repo-url> quantdss
cd quantdss
cp .env.example .env
# Edit .env with your broker credentials, DB passwords, Telegram bot token

# 2. Start the stack
docker-compose up -d

# 3. Run migrations
docker-compose exec backend alembic upgrade head

# 4. Seed default data
docker-compose exec backend python -m scripts.seed_defaults

# 5. Download historical data (one-time)
docker-compose exec backend python -m scripts.download_history

# 6. Access
# Dashboard:  http://localhost
# API Docs:   http://localhost:8000/docs
```

## Architecture

```text
 ┌──────────────┐     ┌──────────────┐     ┌──────────────┐
 │ Broker APIs  │────►│ Tick Normal. │────►│ Candle Aggr. │
 └──────┬───────┘     └──────────────┘     └──────┬───────┘
        │                                         │
        ▼                                         ▼
 ┌──────────────┐     ┌──────────────┐     ┌──────────────┐
 │ yfinance API │     │  Risk Engine │◄────│ Strategy Eng.│
 └──────────────┘     └──────┬───────┘     └──────────────┘
                             │
                             ▼
 ┌──────────────┐     ┌──────────────┐     ┌──────────────┐
 │  PostgreSQL  │◄────│ Alert Dispa. │────►│ SSE Streamer │──► Dashboard
 └──────────────┘     └──────┬───────┘     └──────────────┘
                             │
                             └───► Telegram ──► Phone
```

## Project Structure

```
quantdss/
├── backend/          # FastAPI Python application
│   └── scripts/      # Utility scripts for seeding and history
├── frontend/         # Next.js 14 TypeScript application
├── nginx/            # Reverse proxy config
├── problem statement/# Specification documents
├── docker-compose.yml
└── .env.example
```

## License

Personal use only. Not for commercial distribution or advisory services.

<br/>
<div align="center">
  Built with ❤️ by <a href="https://github.com/mohd98zaid">Mohd Zaid</a>.
  <br/><br/>
  <strong>⭐ Star this repo to show your support! ⭐</strong>
</div>
