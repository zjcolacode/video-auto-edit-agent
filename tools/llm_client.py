"""LLM client over the OpenAI-compatible Coding Plan endpoint.

Aliyun Bailian "Coding Plan" exposes models such as ``qwen3.6-plus`` via
an OpenAI-compatible REST API at ``https://coding.dashscope.aliyuncs.com/v1``.
This client wraps the standard ``openai`` SDK so the rest of the agent
pipeline does not depend on any vendor-specific SDK.
"""
from __future__ import annotations

import json
import re
from typing import List, Optional

from openai import OpenAI

from config import settings
from schemas import VideoAnalysis


# Coding Plan 中具备视觉理解能力的模型（用于视频帧分析）。
# 注意：qwen3-max / qwen3-coder-* / glm-5 / glm-4.7 / MiniMax-M2.5 均无视觉能力，
# 因此故意不放入别名表，避免 CLI 误选导致请求失败。
_MODEL_ALIASES = {
    "plus": "qwen3.6-plus",   # 默认
    "pro":  "qwen3.5-plus",   # 备选/兜底
    "kimi": "kimi-k2.5",      # 备选
}

# 显式列出当前支持视觉理解的全名，便于校验与提示。
VISION_CAPABLE_MODELS = frozenset({
    "qwen3.6-plus",
    "qwen3.5-plus",
    "kimi-k2.5",
})


def resolve_model(alias_or_name: str) -> str:
    return _MODEL_ALIASES.get(alias_or_name, alias_or_name)


class LLMClient:
    def __init__(
        self,
        *,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
    ) -> None:
        key = api_key or settings.coding_api_key
        url = base_url or settings.coding_base_url
        if not key:
            raise RuntimeError(
                "CODING_API_KEY is missing. Set it in .env before running."
            )
        self._client = OpenAI(api_key=key, base_url=url)

    # ---------- structured analysis ----------
    def analyze(
        self,
        prompt: str,
        frames_b64: List[str],
        transcript: Optional[str],
        *,
        model: str,
        temperature: float = 0.3,
    ) -> VideoAnalysis:
        """Send a multi-image request and parse a VideoAnalysis from the reply."""
        if not frames_b64:
            raise ValueError("frames_b64 must contain at least one image")

        content: list[dict] = [{"type": "text", "text": prompt}]
        if transcript:
            content.append(
                {
                    "type": "text",
                    "text": (
                        "\n\n## 视频字幕（ASR 转写，可能含时间戳）\n"
                        "下面这段文本来自原视频的语音转写，请把它与画面信息综合考虑：\n\n"
                        f"{transcript}"
                    ),
                }
            )
        for b64 in frames_b64:
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
                }
            )

        response = self._client.chat.completions.create(
            model=resolve_model(model),
            messages=[{"role": "user", "content": content}],
            temperature=temperature,
            response_format={"type": "json_object"},
        )
        text = response.choices[0].message.content or ""
        payload = self._parse_json(text)
        return VideoAnalysis.model_validate(payload)

    # ---------- generic multimodal JSON completion ----------
    def complete_json(
        self,
        prompt: str,
        frames_b64: List[str],
        *,
        model: str,
        temperature: float = 0.7,
        extra_text: Optional[str] = None,
    ) -> dict:
        """Generic multimodal call returning a parsed JSON object.

        Used by ScriptWriter and other agents that need free-form structured output
        beyond the VideoAnalysis schema.
        """
        if not frames_b64:
            raise ValueError("frames_b64 must contain at least one image")

        content: list[dict] = [{"type": "text", "text": prompt}]
        if extra_text:
            content.append({"type": "text", "text": extra_text})
        for b64 in frames_b64:
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
                }
            )

        response = self._client.chat.completions.create(
            model=resolve_model(model),
            messages=[{"role": "user", "content": content}],
            temperature=temperature,
            response_format={"type": "json_object"},
        )
        text = response.choices[0].message.content or ""
        return self._parse_json(text)

    # ---------- helpers ----------
    @staticmethod
    def _parse_json(text: str) -> dict:
        """Robust JSON extraction: strip code fences, fall back to outermost {}."""
        text = text.strip()
        fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.DOTALL)
        if fence:
            text = fence.group(1).strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", text, re.DOTALL)
            if not match:
                raise
            return json.loads(match.group(0))
