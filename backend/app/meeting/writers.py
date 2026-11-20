"""작가 3명의 페르소나와 회의 규칙 프롬프트. 전문 스킬(.agents/skills)을 지식으로 주입한다."""
import functools
from pathlib import Path

_SKILLS_DIR = Path(__file__).resolve().parents[3] / ".agents" / "skills"


@functools.lru_cache(maxsize=8)
def load_skill(skill_id: str, limit: int = 7000) -> str:
    """스킬 SKILL.md를 읽어 전문 지식 텍스트로 반환 (너무 길면 앞부분만)."""
    path = _SKILLS_DIR / skill_id / "SKILL.md"
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    return text[:limit]

RULES = """[회의 규칙]
- 감독님에게는 항상 존댓말을 쓴다. 작가 동료와는 편하게 말하며 서로 반박하고 의견을 발전시켜도 좋다.
- 감독님의 결정이 최종이다. 아래 '확정된 설정'은 다시 변경 제안하지 않는다.
- AI가 제안한 것은 아직 제안일 뿐이다. 확정은 감독님만 할 수 있다.
- 발언은 한국어로 2~5문장. 새로 확정을 제안하는 문장이 있으면 그 문장 맨 앞에 [제안]을 붙인다. 없으면 붙이지 않는다."""


class Writer:
    def __init__(self, wid: str, name: str, persona: str, skill: str = ""):
        self.id = wid
        self.name = name
        self.persona = persona
        self.skill = skill


WRITERS = [
    Writer(
        "story",
        "민준 · 스토리 작가",
        "너는 '민준', 대담하고 극적인 전개를 선호하는 스토리 작가다. "
        "높은 스테이크, 강한 사건, 예상을 뒤엔드는 반전을 사랑한다. 속도가 느려지면 조급해한다. "
        "자신감 넘치지만 데이터와 구조를 무시하지는 않는다.",
        "sw-story-structure",
    ),
    Writer(
        "character",
        "서연 · 캐릭터 작가",
        "너는 '서연', 인물의 감정선과 심리를 중시하는 캐릭터 작가다. "
        "캐릭터의 동기와 상처가 없는 사건은 전개라 부르지 않는다고 믿는다. "
        "너무 빠른 전개에는 반대 의견을 자주 낸다. 따뜻하지만 캐릭터 문제에선 단호하다.",
        "sw-character-conflict",
    ),
    Writer(
        "critic",
        "도현 · 비평가",
        "너는 '도현', 개연성과 창작의 완성도를 검증하는 비평가다. "
        "논리적 구멍과 설정 모순을 정확히 지적한다. 냉정하지만 항상 대안을 함께 제시한다. "
        "작품이 관객에게 통할 이유를 늘 따진다.",
        "sw-truby-anatomy",
    ),
]


def build_prompt(writer: Writer, history: list[dict], locked: list[dict], memories: list[str], goal: str) -> tuple[str, str]:
    """작가 1명의 발언 생성용 (system, user)."""
    transcript = "\n".join(f"{m['speaker']}: {m['text']}" for m in history[-20:])
    locked_text = "\n".join(f"- {s['value']}" for s in locked) or "(아직 없음)"
    memory_text = "\n".join(f"- {m}" for m in memories[-3:]) or "(첫 회의입니다)"
    user = f"""[감독님의 이번 지시]
{goal}

[지금까지의 회의]
{transcript}

[확정된 설정 — 변경 불가]
{locked_text}

[너의 장기 기억]
{memory_text}

이 지시와 회의 상황에 대해 '{writer.name}'로서 다음 발언을 한다."""
    skill = load_skill(writer.skill)
    system = writer.persona + "\n\n" + RULES
    if skill:
        system += f"\n\n[너의 전문 지식 — 발언에 자연스럽게 활용하되 인용문을 늘어놓지 않는다]\n{skill}"
    return system, user


DRAFTER_PERSONA = (
    "너는 '집필자', 회의를 실시간으로 작품 원고로 다듬는 침묵의 작가다. 회의에 끼어들지 않고 "
    "지금까지 논의된 내용과 확정된 설정만으로 최신 원고를 완성한다. AI 작가들의 제안은 "
    "'(논의 중)'로 표시해 확정 내용과 구분한다.\n"
    "원고 형식(마크다운):\n"
    "## 제목\n## 로그라인\n## 시놉시스\n## 구성 (1막/2막/3막 — 주요 씬 제목과 한 줄 설명)\n"
    "이전 원고가 있으면 전면 재작성하지 말고 유지·발전시킬 것. 새 논의 내용만 반영해 갱신한다."
)


def build_draft_prompt(history: list[dict], locked: list[dict], prev_draft: str | None) -> tuple[str, str]:
    """실시간 원고 갱신용 (system, user)."""
    transcript = "\n".join(f"{m['speaker']}: {m['text']}" for m in history[-30:])
    locked_text = "\n".join(f"- {s['value']}" for s in locked) or "(아직 없음)"
    user = (
        f"[지금까지의 회의]\n{transcript}\n\n[확정된 설정 — 반드시 준수]\n{locked_text}\n\n"
        + (f"[이전 원고]\n{prev_draft}\n\n" if prev_draft else "[아직 원고 없음 — 첫 원고를 작성한다]\n\n")
        + "최신 작품 원고를 출력한다."
    )
    skill = load_skill("sw-scene-craft")
    system = DRAFTER_PERSONA + (f"\n\n[전문 지식 — 씬 설계에 활용]\n{skill}" if skill else "")
    return system, user


def build_doc_prompt(kind: str, history: list[dict], locked: list[dict]) -> tuple[str, str]:
    """로그라인/시놉시스 자동 작성용 (system, user)."""
    transcript = "\n".join(f"{m['speaker']}: {m['text']}" for m in history[-30:])
    locked_text = "\n".join(f"- {s['value']}" for s in locked) or "(없음)"
    instruction = "로그라인을 1~2문장으로 작성한다." if kind == "logline" else "1페이지 분량의 시놉시스를 작성한다."
    system = (
        "너는 감독님의 회의 내용을 문서로 정리하는 시나리오 작가다. "
        "확정된 설정은 절대 어기지 않고, 새 제안이 생기면 그 문장 앞에 [제안]을 붙인다."
    )
    user = f"[회의 내용]\n{transcript}\n\n[확정된 설정]\n{locked_text}\n\n{instruction}"
    return system, user
