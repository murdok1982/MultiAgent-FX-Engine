"""FastAPI server providing REST endpoints and WebSocket for real-time updates."""
from __future__ import annotations
import threading
from pathlib import Path
from typing import Optional, Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
import uvicorn


def create_app(alerts_dispatcher: Optional[Any] = None) -> FastAPI:
    app = FastAPI(
        title="MultiAgent FX Engine",
        version="1.0.0",
        description="Automated EUR/USD trading system dashboard",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # Tighten in production
        allow_methods=["GET", "POST", "PUT"],
        allow_headers=["*"],
    )

    from api.routes import trades, metrics, system, params
    app.include_router(trades.router, prefix="/api/trades", tags=["trades"])
    app.include_router(metrics.router, prefix="/api/metrics", tags=["metrics"])
    app.include_router(system.router, prefix="/api/system", tags=["system"])
    app.include_router(params.router, prefix="/api/params", tags=["params"])

    # Maximum simultaneous WebSocket dashboard connections.
    # Prevents resource exhaustion / DoS from connection flooding.
    _WS_MAX_CONNECTIONS = 20

    @app.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket) -> None:
        # Enforce connection limit before accepting
        current_count = (
            len(alerts_dispatcher._ws_connections) if alerts_dispatcher is not None else 0
        )
        if current_count >= _WS_MAX_CONNECTIONS:
            await websocket.close(code=1008, reason="Too many connections")
            return

        await websocket.accept()
        if alerts_dispatcher is not None:
            alerts_dispatcher.register_websocket(websocket)
        try:
            while True:
                # Read and discard client messages — dashboard is read-only over WS.
                # Use a timeout so a silent client does not hold the slot forever.
                import asyncio as _asyncio
                try:
                    async with _asyncio.timeout(300):  # 5-minute idle timeout
                        await websocket.receive_text()
                except TimeoutError:
                    break  # drop idle connection gracefully
        except WebSocketDisconnect:
            pass
        finally:
            if alerts_dispatcher is not None:
                alerts_dispatcher.unregister_websocket(websocket)

    # Serve static dashboard
    dashboard_path = Path(__file__).parent.parent / "dashboard"
    if dashboard_path.exists():
        app.mount(
            "/",
            StaticFiles(directory=str(dashboard_path), html=True),
            name="dashboard",
        )

    return app


def start_api_server(
    host: str = "0.0.0.0",
    port: int = 8080,
    alerts_dispatcher: Optional[Any] = None,
) -> threading.Thread:
    """Start the API server in a background daemon thread."""
    app = create_app(alerts_dispatcher)

    def _run() -> None:
        uvicorn.run(app, host=host, port=port, log_level="warning")

    thread = threading.Thread(target=_run, daemon=True, name="api-server")
    thread.start()
    return thread
