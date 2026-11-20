"""LLM 프로바이더 어댑터 — Gemini(원작 하드닝 클라이언트 재사용) + OpenAI 호환.

모든 모델 호출은 이 모듈을 통과한다. 공급자 교체는 LLM_PROVIDER 환경변수 하나로.
  * stream_text: 작가 발언용 텍스트 스트리밍. {"text": str} ... 마지막 {"usage": {...}}
  * generate_json: 구조화 문서 생성용. 원작의 하드닝 경로(레이트리밋→재시도→수리)를 통과한다.
"""
from __future__ import annotations

import asyncio
import json
import threading
from typing import AsyncIterator, Type, TypeVar

from pydantic import BaseModel

from app.config import settings

ModelT = TypeVar("ModelT", bound=BaseModel)


class OpenAICompatProvider:
    name = "openai"

    def __init__(self):
        self.base = settings.openai_base_url.rstrip("/")
        self.key = settings.openai_api_key
        self.model = settings.openai_model

    async def stream_text(self, system: str, user: str) -> AsyncIterator[dict]:
        """yields {"text": str} ... 마지막 {"usage": {...}}. 취소는 스트림 중단으로 처리."""
        import httpx

        payload = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        headers = {"Authorization": f"Bearer {self.key}"} if self.key else {}
        async with httpx.AsyncClient(timeout=180) as client:
            async with client.stream("POST", f"{self.base}/chat/completions", json=payload, headers=headers) as r:
                if r.status_code != 200:
                    body = (await r.aread()).decode(errors="replace")
                    yield {"error": f"LLM API 오류 {r.status_code}: {body[:300]}"}
                    yield {"usage": {}}
                    return
                async for line in r.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        obj = json.loads(data)
                    except ValueError:
                        continue
                    if obj.get("usage"):
                        yield {"usage": {
                            "prompt_tokens": obj["usage"].get("prompt_tokens") or 0,
                            "completion_tokens": obj["usage"].get("completion_tokens") or 0,
                        }}
                    choices = obj.get("choices") or []
                    delta = (choices and choices[0].get("delta", {}).get("content")) or ""
                    if delta:
                        yield {"text": delta}

    async def generate_json(self, request_id: str, agent: str, system: str, user: str, schema: Type[ModelT]) -> ModelT:
        """OpenAI 호환 경로: 전체 응답 → 원작의 _extract_json으로 파싱, 1회 수리 재시도."""
        from app.llm.client import _extract_json, _is_retryable

        schema_str = json.dumps(schema.model_json_schema(), ensure_ascii=False)
        contents = (
            f"{user}\n\nReturn ONLY a single JSON object conforming to this JSON Schema. "
            f"No markdown, no commentary.\nSCHEMA:\n{schema_str}"
        )
        for attempt in range(settings.max_retries):
            try:
                answer = await self._full_text(system, contents)
                return schema.model_validate(_extract_json(answer))
            except Exception as exc:  # noqa: BLE001
                if not _is_retryable(exc) or attempt == settings.max_retries - 1:
                    raise
                await asyncio.sleep(min(2 ** attempt, 15))
        raise RuntimeError("unreachable")

    async def _full_text(self, system: str, user: str) -> str:
        buf = []
        async for part in self.stream_text(system, user):
            if "error" in part:
                raise RuntimeError(part["error"])
            if "text" in part:
                buf.append(part["text"])
        return "".join(buf)


class GeminiProvider:
    """원작 GeminiClient를 그대로 재사용하는 어댑터. 토큰 사용량만 서브클래스로 기록."""

    name = "gemini"

    def __init__(self):
        from app.llm.client import GeminiClient

        class UsageClient(GeminiClient):
            last_usage: dict = {}

            def _raw_call(self, system, contents):
                resp = super()._raw_call(system, contents)
                um = getattr(resp, "usage_metadata", None)
                if um:
                    self.last_usage = {
                        "prompt_tokens": getattr(um, "prompt_token_count", 0) or 0,
                        "completion_tokens": getattr(um, "candidates_token_count", 0) or 0,
                    }
                return resp

        self.client = UsageClient()
        self.model = settings.gemini_model

    async def stream_text(self, system: str, user: str) -> AsyncIterator[dict]:
        """동기 SDK 스트리밍을 스레드 → 큐로 넘겨 async 제공. 취소는 큐 소비 중단으로 처리."""
        queue: asyncio.Queue = asyncio.Queue()
        loop = asyncio.get_running_loop()

        def worker():
            def emit(v: dict | None) -> None:
                loop.call_soon_threadsafe(queue.put_nowait, v)

            try:
                cfg = self.client._config(system, settings.enable_thinking)
                usage = None
                for chunk in self.client.client.models.generate_content_stream(
                    model=self.model, contents=user, config=cfg
                ):
                    try:
                        parts = chunk.candidates[0].content.parts or []
                        for p in parts:
                            if getattr(p, "thought", False):
                                continue
                            t = getattr(p, "text", "") or ""
                            if t:
                                emit({"text": t})
                    except Exception:  # noqa: BLE001 - part 구조가 다르면 chunk.text로 폴백
                        t = getattr(chunk, "text", "") or ""
                        if t:
                            emit({"text": t})
                    um = getattr(chunk, "usage_metadata", None)
                    if um:
                        usage = {
                            "prompt_tokens": getattr(um, "prompt_token_count", 0) or 0,
                            "completion_tokens": getattr(um, "candidates_token_count", 0) or 0,
                        }
                emit({"usage": usage or {}})
            except Exception as exc:  # noqa: BLE001
                emit({"error": str(exc)})
            finally:
                emit(None)

        threading.Thread(target=worker, daemon=True).start()
        while True:
            item = await queue.get()
            if item is None:
                break
            yield item

    async def generate_json(self, request_id: str, agent: str, system: str, user: str, schema: Type[ModelT]) -> ModelT:
        # 원작 하드닝 경로(레이트리밋→재시도→수리)를 통째로 통과한다.
        result = await asyncio.to_thread(self.client.generate_json, request_id, agent, system, user, schema)
        if self.client.last_usage:
            self.client.last_usage.setdefault("model", self.model)
        return result


def get_provider():
    if settings.llm_provider == "openai":
        return OpenAICompatProvider()
    return GeminiProvider()
