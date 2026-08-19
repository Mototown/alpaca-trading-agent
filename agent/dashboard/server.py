"""
dashboard/server.py — FastAPI application factory.

Mount routes and static/template directories here.
The app is started as a background task by agent/main.py.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from agent.dashboard.routes import router


def create_app() -> FastAPI:
    app = FastAPI(
        title="DAHA — Dynamic Adaptive Hedging Agent",
        description="Live dashboard for the Alpaca AI Hackathon trading agent.",
        version="0.1.0",
        docs_url="/api/docs",
    )
    app.include_router(router)
    return app
