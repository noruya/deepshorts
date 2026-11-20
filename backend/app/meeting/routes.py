"""회의 REST + WebSocket 라우트."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, WebSocket
from pydantic import BaseModel

from app.meeting.session import get_store, handle_ws

router = APIRouter(prefix="/api/meeting")


class ProjectIn(BaseModel):
    name: str = "내 시나리오"


@router.post("/projects")
def create_project(req: ProjectIn):
    store = get_store()
    pid = uuid.uuid4().hex[:12]
    store.create_project(pid, req.name)
    return {"id": pid, "name": req.name}


@router.get("/projects")
def list_projects():
    return get_store().get_projects()


@router.get("/{pid}/history")
def history(pid: str):
    store = get_store()
    return {"messages": store.get_messages(pid), "settings": store.get_settings(pid),
            "usage": store.get_usage(pid)}


@router.websocket("/ws/{pid}")
async def meeting_ws(ws: WebSocket, pid: str):
    await handle_ws(ws, pid)
