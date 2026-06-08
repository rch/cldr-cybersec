"""Agent configuration (env-sourced; a HOCON layer is added in P4).

Every value has a literal default AND an env override, so the air-gap default
(``backend=null``) needs no configuration at all, and deploy-time values arrive
via the configMap-injected env — which is exactly the ``${?ENV}`` override layer
for the HOCON-at-runtime config added later. Secrets (api_key) come from a K8s
secretRef -> env, never from a ``.conf`` baked into an image layer.
"""
from __future__ import annotations

import os
import shlex
from dataclasses import dataclass, field
from typing import List, Mapping, Optional

_DISABLED = ("", "null", "off", "none", "disabled")


@dataclass
class AgentConfig:
    backend: str = "null"                       # AGENT_BACKEND: "null" | "acp"
    command: List[str] = field(default_factory=lambda: ["vibe-acp"])  # AGENT_ACP_COMMAND
    model_base_url: str = ""                     # AGENT_MODEL_BASE_URL (OpenAI-compatible)
    model_name: str = "mistral"                  # AGENT_MODEL
    api_key: str = ""                            # AGENT_MODEL_API_KEY (often "EMPTY" for vLLM)
    sandbox_root: str = "/tmp/agent-sandbox"     # AGENT_SANDBOX_ROOT
    allow_writes: str = "off"                    # AGENT_ALLOW_WRITES: "off" | "sandbox"
    allow_terminal: bool = False                 # AGENT_ALLOW_TERMINAL
    mcp_servers: List[str] = field(default_factory=list)  # AGENT_MCP_SERVERS (comma-sep)

    @property
    def enabled(self) -> bool:
        return self.backend.strip().lower() not in _DISABLED

    @classmethod
    def from_env(cls, env: Optional[Mapping[str, str]] = None) -> "AgentConfig":
        e = os.environ if env is None else env
        raw_cmd = (e.get("AGENT_ACP_COMMAND") or "").strip()
        return cls(
            backend=(e.get("AGENT_BACKEND") or "null").strip() or "null",
            command=shlex.split(raw_cmd) if raw_cmd else ["vibe-acp"],
            model_base_url=(e.get("AGENT_MODEL_BASE_URL") or "").strip(),
            model_name=(e.get("AGENT_MODEL") or "mistral").strip() or "mistral",
            api_key=(e.get("AGENT_MODEL_API_KEY") or "").strip(),
            sandbox_root=(e.get("AGENT_SANDBOX_ROOT") or "/tmp/agent-sandbox").strip() or "/tmp/agent-sandbox",
            allow_writes=(e.get("AGENT_ALLOW_WRITES") or "off").strip().lower() or "off",
            allow_terminal=(e.get("AGENT_ALLOW_TERMINAL") or "").strip().lower() in ("1", "true", "yes", "on"),
            mcp_servers=[x.strip() for x in (e.get("AGENT_MCP_SERVERS") or "").split(",") if x.strip()],
        )
