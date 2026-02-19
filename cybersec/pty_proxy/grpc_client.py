"""Async gRPC client stub wrapping NavigatorEngine."""

from __future__ import annotations

import logging
from typing import AsyncIterator

import grpc

from cybersec.engine.generated import navigator_pb2 as pb
from cybersec.engine.generated import navigator_pb2_grpc

logger = logging.getLogger(__name__)


class EngineClient:
    """Thin async wrapper around the NavigatorEngine gRPC stub."""

    def __init__(self, host: str = "localhost", port: int = 50051):
        self._target = f"{host}:{port}"
        self._channel: grpc.aio.Channel | None = None
        self._stub: navigator_pb2_grpc.NavigatorEngineStub | None = None

    async def connect(self) -> None:
        self._channel = grpc.aio.insecure_channel(self._target)
        self._stub = navigator_pb2_grpc.NavigatorEngineStub(self._channel)
        logger.info("Connected to engine at %s", self._target)

    async def close(self) -> None:
        if self._channel:
            await self._channel.close()
            self._channel = None
            self._stub = None

    @property
    def stub(self) -> navigator_pb2_grpc.NavigatorEngineStub:
        if self._stub is None:
            raise RuntimeError("Not connected. Call connect() first.")
        return self._stub

    async def execute(self, request: pb.CommandRequest) -> AsyncIterator[pb.EngineEvent]:
        """Execute a command and yield streaming EngineEvents."""
        async for event in self.stub.Execute(request):
            yield event

    async def status(self) -> pb.StatusResponse:
        """Get engine status."""
        return await self.stub.Status(pb.StatusRequest())

    async def cancel(self, session_id: str, command_id: str) -> None:
        """Cancel an in-flight command."""
        await self.stub.Cancel(pb.CancelRequest(
            session_id=session_id,
            command_id=command_id,
        ))
