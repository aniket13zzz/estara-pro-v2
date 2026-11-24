"""Admin-only diagnostics: the demo check (app/channels/vapi/doctor.py) from a browser.

Free cloud hosts have no shell, so ``GET /api/v1/diagnostics`` (signed in as client_admin, for
example through the dashboard address) runs the same checks as the command line and returns the
report. It never contains keys or the shared secret.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from app.auth.deps import AdminUser
from app.channels.vapi import doctor

router = APIRouter(prefix="/api/v1", tags=["diagnostics"])


@router.get("/diagnostics")
async def diagnostics(request: Request, auth: AdminUser) -> dict[str, Any]:
    ok, text = await doctor.report(request.app.state.settings)
    return {"ok": ok, "report": text}
