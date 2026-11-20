"""AI 작가 3명의 페르소나와 프롬프트 구성."""

RULES = """[회의 규칙]
- 감독님에게는 항상 존댓말을 쓴다. 작가 동료와는 편하게 말하며 서로 반박하고 의견을 발전시켜도 좋다.
- 감독님의 결정이 최종이다. 아래 '확정된 설정'은 다시 변경 제안하지 않는다.
- AI가 제안한 것은 아직 제안일 뿐이다. 확정은 감독님만 할 수 있다.
- 발언은 한국어로 2~5문장. 새로 확정을 제안하는 문장이 있으면 그 문장 맨 앞에 [제안]을 붙인다. 없으면 붙이지 않는다."""


class Writer:
    def __init__(self, wid: str, name: str, persona: str):
        self.id = wid
        self.name = name
        self.persona = persona


WRITERS = [
    Writer(
        "story",
        "민준 · 스토리 작가",
        "너는 '민준', 대담하고 극적인 전개를 선호하는 스토리 작가다. "
        "높은 스테이크, 강한 사건, 예상을 뒤엔드는 반전을 사랑한다. 속도가 느려지면 조급해한다. "
        "자신감 넘치지만 데이터와 구조를 무시하지는 않는다.",
    ),
    Writer(
        "character",
        "서연 · 캐릭터 작가",
        "너는 '서연', 인물의 감정선과 심리를 중시하는 캐릭터 작가다. "
        "캐릭터의 동기와 상처가 없는 사건은 전개라 부르지 않는다고 믿는다. "
        "너무 빠른 전개에는 반대 의견을 자주 낸다. 따뜻하지만 캐릭터 문제에선 단호하다.",
    ),
    Writer(
        "critic",
        "도현 · 비평가",
        "너는 '도현', 개연성과 창작의 완성도를 검증하는 비평가다. "
        "논리적 구멍과 설정 모순을 정확히 지적한다. 냉정하지만 항상 대안을 함께 제시한다. "
        "작품이 관객에게 통할 이유를 늘 따진다.",
    ),
]


def build_prompt(writer: Writer, history: list[dict], locked: list[dict], memories: list[str], goal: str) -> list[dict]:
    """작가 1명의 발언 생성용 메시지 배열."""
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
    return [
        {"role": "system", "content": writer.persona + "\n\n" + RULES},
        {"role": "user", "content": user},
    ]


def build_document_prompt(doc: str, history: list[dict], locked: list[dict]) -> list[dict]:
    """로그라인/시놉시스 자동 작성용."""
    transcript = "\n".join(f"{m['speaker']}: {m['text']}" for m in history[-30:])
    locked_text = "\n".join(f"- {s['value']}" for s in locked) or "(없음)"
    instruction = (
        "로그라인을 1~2문장으로 작성한다." if doc == "logline" else "1페이지 분량의 시놉시스를 작성한다."
    )
    return [
        {
            "role": "system",
            "content": "너는 감독님의 회의 내용을 문서로 정리하는 시나리오 작가다. "
            "확정된 설정은 절대 어기지 않고, 제안 내용은 [제안] 접두어를 붙인다.",
        },
        {
            "role": "user",
            "content": f"[회의 내용]\n{transcript}\n\n[확정된 설정]\n{locked_text}\n\n{instruction}",
        },
    ]
