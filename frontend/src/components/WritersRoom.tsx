// AI 작가회의 화면 — WebSocket 실시간 채팅 + 감독 제어판 + 스토리 바이블.
import { useCallback, useEffect, useRef, useState } from "react";
import { createMeetingProject, meetingWsUrl } from "../api";
import type { LockedSetting, MeetingMessage, MeetingState, MeetingUsage } from "../types";

const WRITERS = [
  { name: "민준", tag: "스토리 작가", color: "var(--violet)" },
  { name: "서연", tag: "캐릭터 작가", color: "var(--crimson)" },
  { name: "도현", tag: "비평가", color: "#5ed18a" },
];

const STATE_LABEL: Record<MeetingState, string> = {
  connecting: "연결 중…",
  running: "회의 진행 중",
  paused: "일시정지",
  waiting_approval: "감독 승인 대기",
  stopped: "회의 종료",
};

function speakerColor(speaker: string): string {
  const w = WRITERS.find((w) => speaker.startsWith(w.name));
  if (w) return w.color;
  if (speaker === "감독") return "var(--gold)";
  return "var(--gold-soft)";
}

function fmtTime(ts: number): string {
  return new Date(ts * 1000).toLocaleTimeString("ko-KR", { hour: "2-digit", minute: "2-digit" });
}

interface Props {
  onSwitch: () => void;
}

export default function WritersRoom({ onSwitch }: Props) {
  const [pid, setPid] = useState<string | null>(() => localStorage.getItem("wr_project_id"));
  const [messages, setMessages] = useState<MeetingMessage[]>([]);
  const [locked, setLocked] = useState<LockedSetting[]>([]);
  const [draft, setDraft] = useState<string | null>(null);
  const [draftStream, setDraftStream] = useState<string | null>(null);
  const [drafting, setDrafting] = useState(false);
  const [usage, setUsage] = useState<MeetingUsage | null>(null);
  const [state, setState] = useState<MeetingState>("connecting");
  const [stream, setStream] = useState<{ speaker: string; text: string } | null>(null);
  const [input, setInput] = useState("");
  const [error, setError] = useState<string | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const listRef = useRef<HTMLDivElement>(null);

  const running = state === "running";
  const paused = state === "paused";
  const busy = running || paused;

  // ---- WebSocket 연결 + 수신 루프 ----
  useEffect(() => {
    let ws: WebSocket | null = null;
    let cancelled = false;

    (async () => {
      let id = localStorage.getItem("wr_project_id");
      if (!id) {
        const p = await createMeetingProject("내 시나리오");
        id = p.id;
        localStorage.setItem("wr_project_id", id);
      }
      if (cancelled || !id) return;
      setPid(id);

      ws = new WebSocket(meetingWsUrl(id));
      wsRef.current = ws;
      ws.onmessage = (e) => {
        let evt: any;
        try {
          evt = JSON.parse(e.data);
        } catch {
          return;
        }
        switch (evt.type) {
          case "history":
            setMessages(evt.messages);
            setLocked(evt.settings.filter((s: any) => s.key !== "draft"));
            setDraft((evt.settings.find((s: any) => s.key === "draft") || {}).value || null);
            break;
          case "message":
            setMessages((prev) => [...prev, evt as MeetingMessage]);
            setStream((s) => (s && evt.speaker.startsWith(s.speaker.split(" ")[0]) ? null : s));
            break;
          case "chunk":
            setStream((prev) =>
              prev && prev.speaker === evt.speaker
                ? { speaker: evt.speaker, text: prev.text + evt.text }
                : { speaker: evt.speaker, text: evt.text },
            );
            break;
          case "speaking":
            setStream(null);
            break;
          case "draft_chunk":
            setDraftStream((prev) => (prev || "") + evt.text);
            break;
          case "draft":
            setDraft(evt.text);
            setDraftStream(null);
            break;
          case "drafting":
            setDrafting(evt.on);
            if (!evt.on) setDraftStream(null);
            break;
          case "state":
            setState(evt.state);
            setUsage(evt.usage);
            break;
          case "locked":
            setLocked((prev) => [...prev, { key: evt.key, value: evt.value, ts: Date.now() / 1000 }]);
            setMessages((prev) => prev.map((m) => (m.id === evt.id ? { ...m, status: "approved" } : m)));
            break;
          case "error":
            setError(evt.text);
            break;
        }
      };
      ws.onclose = () => {
        if (!cancelled) setState("stopped");
      };
    })();

    return () => {
      cancelled = true;
      ws?.close();
    };
  }, []);

  // ---- 새 내용 도착 시 하단 스크롤 ----
  useEffect(() => {
    listRef.current?.scrollTo({ top: listRef.current.scrollHeight, behavior: "smooth" });
  }, [messages, stream]);

  const send = useCallback((obj: Record<string, unknown>) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) wsRef.current.send(JSON.stringify(obj));
  }, []);

  function sendDirector() {
    const text = input.trim();
    if (!text) return;
    send({ type: "director", text });
    setInput("");
  }

  function approve(m: MeetingMessage) {
    send({ type: "approve", id: m.id, key: m.kind || "decision" });
  }

  return (
    <div className="app room-app">
      <div className="bg-aurora" aria-hidden="true">
        <span className="blob blob-a" />
        <span className="blob blob-b" />
        <span className="blob blob-c" />
      </div>

      <header className="topbar">
        <span className="brand">
          <span className="brand-emoji">🎬</span> AI 작가의 방
          <span className="brand-sub">Writers' Room</span>
        </span>
        <div className="topbar-right">
          <span className={`status-pill ${state === "connecting" ? "checking" : running ? "online" : ""}`}>
            <span className="status-dot" />
            {STATE_LABEL[state]}
          </span>
          {usage && (
            <span className="status-pill" title="모든 모델 호출의 토큰 사용량 기록">
              호출 {usage.calls}회 · {usage.prompt_tokens.toLocaleString()}+{usage.completion_tokens.toLocaleString()} tok
            </span>
          )}
          <button type="button" className="btn-ghost mode-switch" onClick={onSwitch} title="DeepShorts 생성기로 전환">
            🎞️ DeepShorts 생성기
          </button>
        </div>
      </header>

      {error && (
        <div className="error-banner" role="alert">
          {error}
          <button type="button" onClick={() => setError(null)}>✕</button>
        </div>
      )}

      <main className="room-layout">
        <section className="room-chat">
          <div className="room-messages" ref={listRef}>
            {messages.length === 0 && !stream && (
              <div className="empty-state room-empty">
                <div className="empty-reel">🎥</div>
                <h2>첫 지시로 회의를 시작하세요</h2>
                <p>예: "좀비가 등장하는 로맨스 코미디 영화를 만들고 싶습니다."</p>
              </div>
            )}
            {messages.map((m) => (
              <div key={m.id} className={`msg ${m.role === "director" ? "msg-director" : ""}`}>
                <div className="msg-head">
                  <span className="msg-speaker" style={{ color: speakerColor(m.speaker) }}>
                    {m.speaker}
                  </span>
                  <span className="msg-time">{fmtTime(m.ts)}</span>
                  {m.status === "proposed" && (
                    <>
                      <span className="msg-badge">제안</span>
                      <button type="button" className="approve-btn" onClick={() => approve(m)}>
                        승인
                      </button>
                    </>
                  )}
                  {m.status === "approved" && <span className="msg-badge msg-badge-locked">확정됨 🔒</span>}
                </div>
                <div className="msg-text">{m.text}</div>
              </div>
            ))}
            {stream && (
              <div className="msg">
                <div className="msg-head">
                  <span className="msg-speaker" style={{ color: speakerColor(stream.speaker) }}>
                    {stream.speaker}
                  </span>
                  <span className="msg-time typing">발언 중…</span>
                </div>
                <div className="msg-text">
                  {stream.text}
                  <span className="cursor">▌</span>
                </div>
              </div>
            )}
          </div>

          <div className="room-input-row">
            <textarea
              className="room-input"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  sendDirector();
                }
              }}
              rows={2}
              placeholder={busy ? "개입: 지금 입력하면 다음 발언에 우선 반영됩니다" : "감독님의 지시를 입력하세요"}
            />
            <div className="room-actions">
              <button type="button" className="btn-primary" onClick={sendDirector} disabled={!input.trim()}>
                {running ? "개입" : "지시하기"}
              </button>
              <button type="button" className="btn-ghost" onClick={() => send({ type: "pause" })} disabled={!running}>
                ⏸ 일시정지
              </button>
              <button type="button" className="btn-ghost" onClick={() => send({ type: "resume" })} disabled={!paused}>
                ▶ 재개
              </button>
              <button type="button" className="btn-danger" onClick={() => send({ type: "stop" })} disabled={!busy}>
                ⏹ 중단
              </button>
            </div>
            <div className="room-doc-row">
              <button type="button" className="btn-ghost small" onClick={() => send({ type: "logline" })} disabled={busy}>
                📝 로그라인 작성
              </button>
              <button type="button" className="btn-ghost small" onClick={() => send({ type: "synopsis" })} disabled={busy}>
                📄 시놉시스 작성
              </button>
            </div>
          </div>
        </section>

        <aside className="room-side">
          <h3 className="side-title">작가 상태</h3>
          {WRITERS.map((w) => (
            <div key={w.name} className={`writer-card ${stream?.speaker.startsWith(w.name) ? "speaking" : ""}`}>
              <span className="writer-dot" style={{ background: w.color }} />
              <div>
                <div className="writer-name">{w.name}</div>
                <div className="writer-tag">{w.tag}</div>
              </div>
            </div>
          ))}
          <h3 className="side-title">작품 원고 {drafting ? "· 집필 중…" : "(실시간)"}</h3>
          <div className="draft-panel">
            {draftStream ? (
              <pre className="draft-text">{draftStream}<span className="cursor">▌</span></pre>
            ) : draft ? (
              <pre className="draft-text">{draft}</pre>
            ) : (
              <p className="side-empty">첫 라운드가 끝나면 집필자가 회의 내용으로 원고를 만듭니다.</p>
            )}
            {draft && !busy && !drafting && (
              <button type="button" className="approve-btn draft-approve" onClick={() => send({ type: "approve_draft" })}>
                🎬 이 원고를 시나리오로 확정
              </button>
            )}
          </div>
          <h3 className="side-title">스토리 바이블 (확정 설정)</h3>          {locked.length === 0 ? (
            <p className="side-empty">아직 확정된 설정이 없습니다. 제안을 승인하면 이곳에 고정됩니다.</p>
          ) : (
            locked.map((s, i) => (
              <div key={i} className="bible-item">
                <span className="bible-key">
                  {s.key === "decision" ? "감독 결정" : s.key === "logline" ? "로그라인" : s.key === "synopsis" ? "시놉시스" : s.key}
                </span>
                <p>{s.value}</p>
              </div>
            ))
          )}
        </aside>
      </main>
    </div>
  );
}
