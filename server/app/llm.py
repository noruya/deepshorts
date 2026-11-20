"""교체 가능한 LLM 인터페이스. 기본은 OpenAI 호환 어댑터(환경변수로 공급자 교체)."""
import json
import os
from typing import AsyncIterator


class OpenAICompatProvider:
    name = "openai-compat"

    def __init__(self):
        self.base = os.environ.get("LLM_BASE_URL", "https://api.openai.com/v1").rstrip("/")
        self.key = os.environ.get("LLM_API_KEY", "")
        self.model = os.environ.get("LLM_MODEL", "gpt-4o-mini")

    async def stream(self, messages: list[dict]) -> AsyncIterator[dict]:
        """yields {"delta": str} ... 마지막에 {"usage": {...}}"""
        import httpx

        if not self.key:
            async for part in self._dummy(messages):
                yield part
            return

        payload = {
            "model": self.model,
            "messages": messages,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        headers = {"Authorization": f"Bearer {self.key}"}
        async with httpx.AsyncClient(timeout=180) as client:
            async with client.stream("POST", f"{self.base}/chat/completions", json=payload, headers=headers) as r:
                if r.status_code != 200:
                    body = (await r.aread()).decode(errors="replace")
                    raise RuntimeError(f"LLM API 오류 {r.status_code}: {body[:300]}")
                async for line in r.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    obj = json.loads(data)
                    if obj.get("usage"):
                        yield {"usage": obj["usage"]}
                    choices = obj.get("choices") or []
                    delta = (choices and choices[0].get("delta", {}).get("content")) or ""
                    if delta:
                        yield {"delta": delta}

    async def _dummy(self, messages: list[dict]) -> AsyncIterator[dict]:
        """API 키 없을 때의 더미 응답 (UI/흐름 테스트용)."""
        text = (
            "[제안] 감독님, LLM_API_KEY가 설정되지 않아 더미 응답으로 회의를 진행합니다. "
            "키를 설정하면 실제 AI 작가들의 토론이 시작됩니다."
        )
        for i in range(0, len(text), 12):
            yield {"delta": text[i : i + 12]}
        yield {"usage": {"prompt_tokens": 0, "completion_tokens": 0}}


def get_provider() -> OpenAICompatProvider:
    # 새 공급자 추가 시: 이 함수에서 환경변수(Llm_PROVIDER)에 따라 다른 어댑터 반환
    return OpenAICompatProvider()
