"""
ML Training Pipeline Scaffold — Phase 9.

Prepares infrastructure for ML model training:
  - Signal feature logging (links to DB columns already present)
  - Trade outcome linking
  - Dataset export
  - Placeholder for model training (XGBoost, LightGBM, RandomForest)

Run:
    python -m app.workers.ml_pipeline export    — export training dataset
    python -m app.workers.ml_pipeline train     — train model (future)
"""
from __future__ import annotations

import csv
import sys
from datetime import datetime, timezone

from sqlalchemy import select, and_, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import async_session_factory
from app.core.logging import logger
from app.models.signal import Signal as SignalModel
from app.models.paper_trade import PaperTrade
from app.workers.base import WorkerBase


class MLPipeline(WorkerBase):
    """ML training data pipeline for QuantDSS."""

    NAME = "ml-pipeline"

    async def export_dataset(self, output_path: str = "training_data.csv"):
        """
        Export signal features + trade outcomes to CSV.

        Columns:
          signal_type, entry_price, stop_loss, target_price, risk_reward,
          confidence_score, atr_value, quality_score, ml_probability,
          trade_outcome (win/loss/no_trade), realised_pnl
        """
        logger.info(f"[{self.NAME}] Exporting training dataset to {output_path}")

        async with async_session_factory() as db:
            # Fetch all signals with their associated trades
            result = await db.execute(
                select(SignalModel).order_by(SignalModel.timestamp.asc())
            )
            signals = result.scalars().all()

            rows = []
            for sig in signals:
                # Try to find a matching paper trade
                trade_result = await db.execute(
                    select(PaperTrade).where(
                        and_(
                            PaperTrade.direction == sig.signal_type,
                            PaperTrade.entry_price == sig.entry_price,
                            PaperTrade.status == "CLOSED",
                        )
                    ).limit(1)
                )
                trade = trade_result.scalar_one_or_none()

                outcome = "no_trade"
                pnl = 0.0
                if trade:
                    pnl = getattr(trade, "realised_pnl", 0.0) or 0.0
                    outcome = "win" if pnl > 0 else "loss"

                # Parse score breakdown
                quality = 0
                ml_prob = 0.0
                try:
                    import json
                    if sig.score_breakdown:
                        bd = json.loads(sig.score_breakdown) if isinstance(sig.score_breakdown, str) else sig.score_breakdown
                        quality = bd.get("quality_score", 0) or 0
                        ml_prob = bd.get("ml_probability", 0) or 0
                except Exception:
                    pass

                rows.append({
                    "timestamp": sig.timestamp.isoformat() if sig.timestamp else "",
                    "signal_type": sig.signal_type,
                    "entry_price": sig.entry_price,
                    "stop_loss": sig.stop_loss,
                    "target_price": sig.target_price,
                    "risk_reward": sig.risk_reward,
                    "confidence_score": sig.confidence_score,
                    "atr_value": sig.atr_value,
                    "quality_score": quality,
                    "ml_probability": ml_prob,
                    "risk_status": sig.risk_status,
                    "trade_outcome": outcome,
                    "realised_pnl": pnl,
                })

            # Write CSV
            if rows:
                with open(output_path, "w", newline="") as f:
                    writer = csv.DictWriter(f, fieldnames=rows[0].keys())
                    writer.writeheader()
                    writer.writerows(rows)
                logger.info(f"[{self.NAME}] Exported {len(rows)} rows to {output_path}")
            else:
                logger.warning(f"[{self.NAME}] No signals found — empty dataset")

    async def train_model(self):
        """
        Placeholder for model training.

        Future implementation:
          1. Load training_data.csv
          2. Feature engineering
          3. Train XGBoost / LightGBM / RandomForest
          4. Save model to disk
          5. Update MLFilter inference path
        """
        logger.info(f"[{self.NAME}] Model training not yet implemented")
        logger.info(f"[{self.NAME}] Planned models: XGBoost, LightGBM, RandomForest")
        logger.info(f"[{self.NAME}] Export dataset first with: python -m app.workers.ml_pipeline export")

    async def run(self):
        """Entry point — dispatch based on CLI args."""
        if len(sys.argv) > 1:
            command = sys.argv[1]
            if command == "export":
                output = sys.argv[2] if len(sys.argv) > 2 else "training_data.csv"
                await self.export_dataset(output)
            elif command == "train":
                await self.train_model()
            else:
                logger.error(f"Unknown command: {command}. Use 'export' or 'train'.")
        else:
            logger.info(f"[{self.NAME}] Usage: python -m app.workers.ml_pipeline [export|train]")


# ── Entry Point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    MLPipeline().main()
