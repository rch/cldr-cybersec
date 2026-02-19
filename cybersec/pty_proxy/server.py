"""PTY proxy WebSocket server — bridges ghostty-web terminal to gRPC engine.

Usage:
    uv run python -m cybersec.pty_proxy.server --ws-port=8765 --engine-host=localhost --engine-port=50051
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import signal

import websockets
from websockets.asyncio.server import serve as ws_serve

from cybersec.pty_proxy.grpc_client import EngineClient
from cybersec.pty_proxy.repl import NavigatorREPL

logger = logging.getLogger(__name__)


async def ws_handler(websocket, engine: EngineClient):
    """Handle a single WebSocket connection from ghostty-web."""
    repl = NavigatorREPL(engine)
    remote = websocket.remote_address
    logger.info("New terminal connection from %s", remote)

    # Send welcome banner
    await websocket.send(json.dumps({"type": "text", "data": repl.get_banner()}))

    try:
        async for raw_msg in websocket:
            try:
                frame = json.loads(raw_msg)
            except json.JSONDecodeError:
                continue

            if frame.get("type") == "input":
                data = frame.get("data", "")
                async for out_frame in repl.feed(data):
                    await websocket.send(json.dumps(out_frame))

            elif frame.get("type") == "resize":
                # Terminal resize — noted but not acted on yet
                pass

    except websockets.ConnectionClosed:
        logger.info("Terminal disconnected: %s", remote)
    except Exception:
        logger.exception("WebSocket handler error")


async def run_server(ws_port: int, engine_host: str, engine_port: int) -> None:
    """Start the WebSocket server and connect to the gRPC engine."""
    engine = EngineClient(host=engine_host, port=engine_port)
    await engine.connect()
    logger.info("Engine connected: %s:%d", engine_host, engine_port)

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop_event.set)

    async def handler(ws):
        await ws_handler(ws, engine)

    async with ws_serve(handler, "0.0.0.0", ws_port):
        logger.info("PTY proxy listening on ws://0.0.0.0:%d/", ws_port)
        await stop_event.wait()

    await engine.close()
    logger.info("PTY proxy stopped.")


def main():
    parser = argparse.ArgumentParser(description="PTY proxy WebSocket server")
    parser.add_argument("--ws-port", type=int, default=8765, help="WebSocket listen port (default: 8765)")
    parser.add_argument("--engine-host", type=str, default="localhost", help="gRPC engine host")
    parser.add_argument("--engine-port", type=int, default=50051, help="gRPC engine port")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    asyncio.run(run_server(args.ws_port, args.engine_host, args.engine_port))


if __name__ == "__main__":
    main()
