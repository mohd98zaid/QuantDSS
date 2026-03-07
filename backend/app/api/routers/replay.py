"""
Replay API Router — Endpoints to manage the Market Replay Engine.
"""
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, conlist

from app.replay.replay_controller import ReplayController, fetch_replay_summary
from app.core.logging import logger

router = APIRouter()

class ReplayStartRequest(BaseModel):
    csv_data: str
    replay_speed: int = 1

@router.post("/start", status_code=200)
async def start_replay(request: ReplayStartRequest):
    """
    Start a new Market Replay session.
    Requires TradingMode to be set to PAPER.
    """
    try:
        session_id = await ReplayController.start(request.csv_data, request.replay_speed)
        return {"status": "success", "message": "Replay started", "session_id": session_id}
    except Exception as e:
        logger.exception("Failed to start replay")
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/pause", status_code=200)
async def pause_replay():
    """Pause the current Market Replay session."""
    try:
        ReplayController.pause()
        return {"status": "success", "message": "Replay paused"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/resume", status_code=200)
async def resume_replay():
    """Resume a paused Market Replay session."""
    try:
        await ReplayController.resume()
        return {"status": "success", "message": "Replay resumed"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/stop", status_code=200)
async def stop_replay():
    """Stop the current Market Replay session and return final metrics."""
    try:
        metrics = ReplayController.stop()
        return {"status": "success", "message": "Replay stopped", "data": metrics}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.get("/status", status_code=200)
async def get_replay_status():
    """Get the status and real-time metrics of the current Market Replay session."""
    try:
        return ReplayController.status()
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.get("/summary/{session_id}", status_code=200)
async def get_replay_summary(session_id: str):
    """Fetch the final generated summary of paper trades for a completed replay session."""
    try:
        summary = await fetch_replay_summary(session_id)
        return {"status": "success", "data": summary}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
