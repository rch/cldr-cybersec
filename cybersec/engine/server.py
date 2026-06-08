"""NavigatorEngine gRPC server entry point.

Usage:
    uv run python -m cybersec.engine.server --port=50051
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal

import grpc

from cybersec.engine.generated import navigator_pb2_grpc
from cybersec.engine.service import NavigatorEngineServicer
from cybersec.engine.agent.authz import AuthzInterceptor

logger = logging.getLogger(__name__)


async def serve(port: int = 50051) -> None:
    """Start the gRPC server."""
    # The authz interceptor is scoped to ONLY the agent RPC (the Atlas/Ranger
    # seam); Execute/Status/Subscribe/Cancel pass through untouched.
    server = grpc.aio.server(interceptors=[AuthzInterceptor()])
    servicer = NavigatorEngineServicer()
    navigator_pb2_grpc.add_NavigatorEngineServicer_to_server(servicer, server)
    listen_addr = f"[::]:{port}"
    server.add_insecure_port(listen_addr)

    await server.start()
    logger.info("NavigatorEngine listening on %s", listen_addr)

    # Graceful shutdown on SIGTERM/SIGINT
    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    def _signal_handler():
        logger.info("Shutdown signal received")
        stop_event.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _signal_handler)

    await stop_event.wait()
    logger.info("Shutting down gracefully (5s grace)...")
    await server.stop(grace=5)
    await servicer.aclose()
    logger.info("Server stopped.")


def main():
    parser = argparse.ArgumentParser(description="NavigatorEngine gRPC server")
    parser.add_argument("--port", type=int, default=50051, help="Listen port (default: 50051)")
    parser.add_argument("--verbose", "-v", action="store_true", help="Debug logging")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    asyncio.run(serve(port=args.port))


if __name__ == "__main__":
    main()
