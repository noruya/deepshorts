"""작가회의 루프 자가검증 — 더미 LLM으로 토론·승인·기억·비용·중단을 확인한다.

실행: cd backend && uv run python tests/test_meeting.py
"""
import asyncio
import json

TEST_TEXT = "[제안] 테스트 발언입니다. 감독님, 의견을 주세요."


class DummyLLM:
    name = "dummy"

    async def stream_text(self, system: str, user: str):
        for i in range(0, len(TEST_TEXT), 8):
            yield {"text": TEST_TEXT[i : i + 8]}
        yield {"usage": {"prompt_tokens": 10, "completion_tokens": 5}}


class FakeWS:
    def __init__(self):
        self.sent = []

    async def send_text(self, t: str):
        self.sent.append(json.loads(t))


async def main():
    from app.meeting.session import MeetingSession
    from app.meeting.storage import Store

    s = MeetingSession(FakeWS(), "t1")
    s.store = Store(":memory:")
    s.store.create_project("t1", "테스트")
    s.llm = DummyLLM()

    # 1) 토론 루프: 감독 지시 1회 → 작가 3명 × 2라운드 = 6발언, 모두 제안 상태
    await s.handle({"type": "director", "text": "좀비 로맨스 코미디로 시작하자"})
    assert s.task, "라운드 태스크가 시작돼야 한다"
    await s.task
    msgs = [m for m in s.store.get_messages("t1") if m["role"] == "writer"]
    assert len(msgs) == 6, f"writer 발언 수: {len(msgs)}"
    assert all(m["status"] == "proposed" for m in msgs), "[제안] 파싱 실패"
    assert all(m["ts"] > 0 for m in msgs), "타임스탬프 누락"

    # 1b) 집필자: 라운드마다 원고 갱신 → 같은 키는 교체(살아있는 문서)
    draft = s.store.get_setting("t1", "draft")
    assert draft and "[제안]" in draft, "원고 미작성"
    draft_events = [m for m in s.ws.sent if m.get("type") == "draft"]
    assert len(draft_events) == 2, f"라운드 수만큼 원고 갱신: {len(draft_events)}"

    # 1c) 원고 승인 → 시나리오 확정
    await s.handle({"type": "approve_draft"})
    scenario = s.store.get_setting("t1", "scenario")
    assert scenario == draft, "원고 승인 실패"

    # 2) 승인 → 고정 설정 기록
    await s.handle({"type": "approve", "id": msgs[0]["id"], "key": "decision"})
    locked = [x for x in s.store.get_settings("t1") if x["key"] == "decision"]
    assert len(locked) == 1, locked
    locked_msg = [m for m in s.store.get_messages("t1") if m["id"] == msgs[0]["id"]][0]
    assert locked_msg["status"] == "approved"

    # 3) 장기 기억 + 비용 기록 (작가 6회 + 집필자 2회)
    s.save_memories()
    assert s.store.get_memories("t1", "story"), "작가 기억 미저장"
    usage = s.store.get_usage("t1")
    assert usage["calls"] == 8 and usage["prompt_tokens"] == 80, usage

    # 4) 중단: 진행 중 회의를 stop → 취소되고 중단 메시지가 저장된다
    await s.handle({"type": "director", "text": "두 번째 지시"})
    await asyncio.sleep(0.2)
    await s.handle({"type": "stop"})
    assert s.stopped
    assert s.task.cancelled() or s.task.done()
    stop_msgs = [m for m in s.store.get_messages("t1") if m["role"] == "writer"]
    assert len(stop_msgs) >= 6, "중단 시점까지의 발언이 저장되지 않음"

    # 5) 재개: stop 후 새 지시 → 다시 라운드 시작
    s.stopped = False
    await s.handle({"type": "director", "text": "세 번째 지시"})
    assert s.task and not s.task.done()
    await s.task

    # 6) 일시정지/재개 이벤트
    await s.handle({"type": "pause"})
    assert not s.paused.is_set()
    await s.handle({"type": "resume"})
    assert s.paused.is_set()

    print("OK: 루프 6발언 / 승인·고정 / 기억 / 비용 / 중단·재개 모두 통과")


if __name__ == "__main__":
    asyncio.run(main())
