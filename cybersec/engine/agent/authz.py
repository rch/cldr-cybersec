"""Authorization interceptor seam (Cloudera Atlas + Ranger).

v1: pass-through. Scoped to ONLY the agent RPC, so the deterministic REPL path
(Execute/Status/Subscribe/Cancel) is never gated by an external policy service.
This is where, as the implementation matures, caller identity is resolved from
the gRPC metadata, Ranger is consulted for an allow/deny on
(user, resource=AgentSession, action=prompt), and an Atlas audit/lineage event
is emitted.
"""
from __future__ import annotations

import logging

import grpc

logger = logging.getLogger(__name__)


class AuthzInterceptor(grpc.aio.ServerInterceptor):
    """No-op today; the Atlas/Ranger insertion point for the agent RPC."""

    GUARDED = frozenset({"/cybersec.engine.NavigatorEngine/AgentSession"})

    async def intercept_service(self, continuation, handler_call_details):
        if handler_call_details.method in self.GUARDED:
            # TODO(atlas-ranger): identity = _identity_from(invocation_metadata)
            #   if not await ranger_allows(identity, "AgentSession", "prompt"):
            #       return _aborting_handler(grpc.StatusCode.PERMISSION_DENIED)
            #   await atlas_audit(identity, "AgentSession")
            logger.debug("authz seam (pass-through): %s", handler_call_details.method)
        return await continuation(handler_call_details)
