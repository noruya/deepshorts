"""회의 세션 — 토론 루프·발언권·감독 개입·승인/고정·일시정지/재개/중단.

핵심 규칙:
  * 감독 개입은 대화 맥락에 즉시 반영되어 다음 발언보다 우선 처리된다.
  * stop은 진행 중인 LLM 스트림을 취소한다(음성 재생 중단과 별개 — 음성은 미구현 단계).
  * 확정된 설정(승인됨)은 프롬프트의 '변경 불가' 목록으로 작가에게 전달된다.
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid

from fastapi import WebSocket

from app.config import settings
from app.meeting.provider import get_provider
from app.meeting.storage import Store
from app.meeting.writers import WRITERS, build_doc_prompt, build_prompt

DUMMY_TEXT = (
    "[제안] 감독님, LLM API 키가 설정되지 않아 더미 응답으로 회의를 진행합니다. "
    "backend/.env에 키를 설정하면 실제 AI 작가들의 토론이 시작됩니다."
)


def get_store() -> Store:
    global _store
    try:
        return _store
    except NameError:
        _store = Store()
        return _store


class MeetingSession:
    def __init__(self, ws: WebSocket, project_id: str):
        self.ws = ws
        self.pid = project_id
        self.store = get_store()
        self.llm = get_provider()
        self.task: asyncio.Task | None = None
        self.paused = asyncio.Event()
        self.paused.set()
        self.stopped = False

    # ------------------------------------------------------------ transport
    async def send(self, obj: dict) -> None:
        await self.ws.send_text(json.dumps(obj, ensure_ascii=False, default=str))

    async def send_message(self, speaker: str, role: str, text: str, status: str = "normal", kind: str | None = None):
        ts = time.time()
        mid = self.store.add_message(self.pid, speaker, role, text, status)
        msg = {"type": "message", "id": mid, "speaker": speaker, "role": role,
               "text": text, "status": status, "ts": ts}
        if kind:
            msg["kind"] = kind
        await self.send(msg)

    def state_name(self) -> str:
        if self.stopped:
            return "stopped"
        if self.task and not self.task.done():
            return "running" if self.paused.is_set() else "paused"
        return "waiting_approval"

    async def send_state(self):
        await self.send({"type": "state", "state": self.state_name(), "usage": self.store.get_usage(self.pid)})

    # ------------------------------------------------------------ main loop
    async def run(self):
        await self.send({"type": "history", "messages": self.store.get_messages(self.pid),
                         "settings": self.store.get_settings(self.pid)})
        await self.send_state()
        try:
            while True:
                data = await self.ws.receive_json()
                await self.handle(data)
        except Exception:  # noqa: BLE001 - WebSocketDisconnect + json 오류 모두 종료로 처리
            pass
        finally:
            self.save_memories()

    async def handle(self, data: dict):
        t = data.get("type")
        if t == "director":
            await self.on_director(data.get("text", "").strip())
        elif t == "pause":
            self.paused.clear()
            await self.send_state()
        elif t == "resume":
            self.paused.set()
            await self.send_state()
        elif t == "stop":
            await self.on_stop()
        elif t == "approve":
            await self.on_approve(int(data.get("id", 0)), data.get("key") or "decision")
        elif t in ("logline", "synopsis"):
            if not self._busy():
                self.stopped = False
                self.paused.set()
                self.task = asyncio.create_task(self.generate_doc(t))
        else:
            await self.send({"type": "error", "text": f"알 수 없는 요청: {t}"})

    def _busy(self) -> bool:
        return bool(self.task and not self.task.done())

    # ------------------------------------------------------------ handlers
    async def on_director(self, text: str):
        if not text:
            return
        await self.send_message("감독", "director", text)
        if not self._busy():
            # 회의 대기 중이던 새 지시 → 새 라운드 시작. 진행 중이면 개입으로 다음 발언에 반영.
            self.stopped = False
            self.paused.set()
            self.task = asyncio.create_task(self.run_rounds(text))

    async def on_stop(self):
        self.stopped = True
        if self._busy():
            self.task.cancel()  # 진행 중 LLM 스트림 취소
            try:
                await self.task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        self.save_memories()
        await self.send_state()

    async def on_approve(self, msg_id: int, key: str):
        locked = self.store.approve_message(self.pid, msg_id, key)
        if locked:
            await self.send({"type": "locked", "id": locked["id"], "key": locked["key"], "value": locked["value"]})
            await self.send_state()
        else:
            await self.send({"type": "error", "text": "승인할 발언을 찾지 못했습니다."})

    # ------------------------------------------------------------ generation
    async def run_rounds(self, goal: str):
        try:
            for _rnd in range(1, settings.max_rounds + 1):
                for w in WRITERS:
                    await self.paused.wait()          # 일시정지 대기
                    if self.stopped:
                        return
                    await self.turn(w, goal)
            self.save_memories()
            await self.send_state()
        except asyncio.CancelledError:
            raise

    async def turn(self, writer, goal: str):
        history = self.store.get_messages(self.pid)
        locked = self.store.get_settings(self.pid)
        memories = self.store.get_memories(self.pid, writer.id)
        system, user = build_prompt(writer, history, locked, memories, goal)
        await self.send({"type": "speaking", "speaker": writer.name})

        buf: list[str] = []
        got_error = None
        try:
            async for part in self.llm.stream_text(system, user):
                if "error" in part:
                    got_error = part["error"]
                    await self.send({"type": "error", "text": part["error"]})
                    continue
                if "text" in part:
                    buf.append(part["text"])
                    await self.send({"type": "chunk", "speaker": writer.name, "text": part["text"]})
            text = "".join(buf)
            if text:
                status = "proposed" if "[제안]" in text else "normal"
                await self.send_message(writer.name, "writer", text, status)
            elif got_error:
                await self.send({"type": "error", "text": f"{writer.name} 발언 생성 실패: {got_error}"})
            if part.get("usage"):
                self.store.add_usage(self.pid, writer.name, self.llm.name, part["usage"])
        except asyncio.CancelledError:
            if buf:  # 중단돼도 지금까지의 발언은 저장 (모든 발언 기록 원칙)
                await self.send_message(writer.name, "writer", "".join(buf) + " …(감독이 중단함)")
            raise

    async def generate_doc(self, kind: str):
        try:
            history = self.store.get_messages(self.pid)
            locked = self.store.get_settings(self.pid)
            system, user = build_doc_prompt(kind, history, locked)
            await self.send({"type": "speaking", "speaker": "문서 정리 중"})
            buf: list[str] = []
            async for part in self.llm.stream_text(system, user):
                if "error" in part:
                    await self.send({"type": "error", "text": part["error"]})
                    continue
                if "text" in part:
                    buf.append(part["text"])
                    await self.send({"type": "chunk", "speaker": "문서 정리 중", "text": part["text"]})
            text = "".join(buf)
            if not text:
                return
            if "[제안]" not in text:
                text = f"[제안] {text}"
            await self.send_message("문서 작성", "writer", text, "proposed", kind=kind)
            if part.get("usage"):
                self.store.add_usage(self.pid, "문서 작성", self.llm.name, part["usage"])
            await self.send_state()
        except asyncio.CancelledError:
            raise

    # ------------------------------------------------------------ memory
    def save_memories(self):
        """회의 종료 시 작가별 최근 발언을 장기 기억으로 저장(다음 회의 프롬프트에 주입)."""
        history = self.store.get_messages(self.pid)
        for w in WRITERS:
            own = [m["text"] for m in history if m["speaker"] == w.name][-3:]
            if own:
                self.store.save_memory(self.pid, w.id, "\n".join(own))


async def handle_ws(ws: WebSocket, project_id: str):
    await ws.accept()
    await MeetingSession(ws, project_id).run()
