"""
In-room phone AI module manifest (MODULE is read by clefs.app.create_app).

Owned by lane B Concierge + Voice from Wave 1. Permission defaults follow foundation spec C;
the pseudo-role "device" grants a permission to paired shared devices.

Routers: the console (/hotels/{slug}/voice), the Google Gemini Live realtime webhook and
the phone's MCP server (both under /webhooks/voice/). The sideband worker
runs as a worker service of `python -m clefs.worker` (never on Vercel).
"""

from clefs.modules.voice.mcp_server import router as mcp_router
from clefs.modules.voice.router import router
from clefs.modules.voice.webhook import router as webhook_router
from clefs.modules.voice.worker import register as _register_jobs, voice_sideband
from clefs.registry import Module

_register_jobs()

MODULE = Module(
    name="voice",
    routers=[router, webhook_router, mcp_router],
    permissions={
        "voice.view": frozenset({"owner", "gm", "front_desk"}),
        "voice.manage": frozenset({"owner", "gm"}),
    },
    worker_services=[voice_sideband],
)
