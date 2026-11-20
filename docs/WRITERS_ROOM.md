# AI Writers' Room — 아키텍처

원작 **DeepShorts**(parvarora/deepshorts, MIT 문화 유지)를 fork해 개조했다.
원작의 일회성 볼리우드 생성 파이프라인은 그대로 유지되며, 그 위에 **한국어 AI 작가회의** 기능을 별도 모듈로 추가했다.

```
web(=frontend/)  React + Vite + TS + Framer Motion  ←WebSocket→  backend/  FastAPI
   ├─ WritersRoom.tsx  작가회의 UI(채팅·제어·바이블)                 ├─ app/llm/        원작 하드닝 Gemini 클라이언트
   └─ App.tsx         작가의 방 ⇄ DeepShorts 모드 전환               ├─ app/graph/      원작 LangGraph 파이프라인 (볼리우드 생성)
                                                                     └─ app/meeting/    ★ 신규: 작가회의
                                                                          ├─ session.py   토론 루프·개입·승인·일시정지/중단
                                                                          ├─ provider.py  Gemini + OpenAI 호환 어댑터
                                                                          ├─ writers.py   작가 3명 페르소나·프롬프트
                                                                          ├─ storage.py   SQLite 자동 저장
                                                                          └─ routes.py    REST + WS 라우트
```

## 회의 기능 개조 내역 (원작 대비 추가/변경)

| 원작 | 개조 후 |
|---|---|
| 일회성 생성(상황 → 대본) | 대화형 회의: 감독 지시 → 작가 3명 토론 루프(최대 2라운드) |
| 서버 저장 없음(localStorage만) | SQLite 자동 저장: 발언(화자·타임스탬프)·고정 설정·작가 기억·비용 |
| 단일 LLM(리셋 불가) | 감독 개입(다음 발언 우선 반영)·일시정지/재개/중단·LLM 스트림 취소 |
| 없음 | 승인·고정: [제안] 발언을 승인하면 스토리 바이블에 고정, 변경 제안 금지 |
| Gemini 전용 | `LLM_PROVIDER=gemini\|openai`로 공급자 교체 (둘 다 스트리밍) |
| 없음 | 작가별 장기 기억: 회의 종료 시 해당 작가 발언을 저장, 다음 회의 프롬프트에 주입 |
| 없음 | 모든 모델 호출의 토큰 사용량을 usage 테이블에 기록 |

## WebSocket 프로토콜 — `/api/meeting/ws/{pid}` (JSON, 한국어)

클라이언트 → 서버:
- `{type:"director", text}` — 감독 발언. 진행 중이면 개입(다음 발언에 우선 반영), 대기 중이면 새 라운드 시작
- `{type:"approve", id, key?}` — [제안] 발언 승인 → 스토리 바이블 고정
- `{type:"pause"|"resume"|"stop"}` / `{type:"logline"|"synopsis"}`

서버 → 클라이언트:
- `{type:"history", messages, settings}` · `{type:"chunk", speaker, text}`(스트리밍) · `{type:"message", id, speaker, role, text, status, ts}` · `{type:"speaking", speaker}` · `{type:"state", state, usage}` · `{type:"locked", key, value}` · `{type:"error", text}`

status: `normal | proposed | approved` — AI 제안과 감독 승인 설정이 명확히 구분된다.

## 대화 규칙 (writers.py RULES에 내장)

감독에게 존댓말 / 작가끼리는 친근 + 반박 허용 / 감독 최종 결정권 / 확정 설정 변경 금지 / `[제안]` 접두어로 제안 표시.

## 실행

```bash
# backend
cd backend && uv venv && uv pip install -r requirements.txt
cp .env.example .env        # GEMINI_API_KEY 또는 OPENAI_* 설정 (키는 커밋 금지)
uv run uvicorn app.main:app --port 8000
# frontend
cd frontend && npm install && npm run dev   # http://localhost:5173
# 자가검증
cd backend && PYTHONPATH=. uv run python tests/test_meeting.py
```

API 키는 서버 환경변수로만 관리하며 클라이언트로 노출하지 않는다.
음성(STT/TTS)은 다음 단계: 어댑터 인터페이스 뒤에 붙이고, 음성 재생 중단은 LLM 취소와 별도 경로로 구현할 것.

## 구현 단계

1. ✅ fork + 기준선 커밋 / 문서
2. ✅ LLM 어댑터(Gemini + OpenAI 호환) + SQLite 저장소
3. ✅ 작가 3명 텍스트 회의 MVP (토론 루프, 스트리밍)
4. ✅ 개입·승인·고정·일시정지/중단·비용 기록 + 자가검증
5. ✅ 프론트 작가의 방 UI (채팅 스트리밍·제어판·스토리 바이블)
6. ⬜ 음성(STT/TTS), 시나리오 본문(LangGraph 파이프라인 연동), 다중 프로젝트 UI
