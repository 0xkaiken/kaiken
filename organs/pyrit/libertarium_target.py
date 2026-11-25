"""PyRIT PromptTarget adapter backed by Libertarium's rt_send transport.

PyRIT hands every turn to a target as a fully normalized conversation
(system prompt + history + current message). rt_send speaks one stateless
prompt per call, so this adapter flattens the conversation into a labelled
transcript and routes it through the same provider path the gauntlet and the
jailbreak lab already use. Nothing is vendored and no extra API key is needed:
opencode-go turns still bill through the operator's own CLI session.
"""

from __future__ import annotations

import asyncio
import json
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import rt_send  # noqa: E402

from pyrit.models import Message, construct_response_from_request  # noqa: E402
from pyrit.prompt_target import PromptTarget, TargetCapabilities, TargetConfiguration  # noqa: E402

_KNOWN_PROVIDERS = {
    "opencode",
    "opencode-go",
    "openrouter",
    "anthropic",
    "openai",
    "google",
    "xai",
    "meta",
    "mistral",
    "deepseek",
    "custom",
    "qwen",
    "zhipu",
    "glm",
    "z-ai",
    "moonshot",
    "kimi",
    "01-ai",
    "yi",
}

_ROLE_LABELS = {
    "system": "SYSTEM",
    "developer": "DEVELOPER",
    "user": "USER",
    "assistant": "ASSISTANT",
    "simulated_assistant": "ASSISTANT",
    "tool": "TOOL",
}

# The wire format is a single stateless prompt, but the adapter keeps the full
# transcript itself, so from PyRIT's side the target behaves like a chat model
# with editable history and a system prompt.
_CAPABILITIES = TargetCapabilities(
    supports_multi_turn=True,
    supports_system_prompt=True,
    supports_editable_history=True,
)


class LibertariumTarget(PromptTarget):
    """A PyRIT target that sends through ``rt_send.send_to_target``."""

    def __init__(
        self,
        *,
        model: str,
        provider: str | None = None,
        timeout: int | None = None,
        label: str = "",
        request_log: Path | None = None,
        verbose: bool = False,
    ) -> None:
        provider, model_id = self._split_model(model, provider)
        super().__init__(
            verbose=verbose,
            endpoint=f"libertarium://{provider}/{model_id}",
            model_name=model_id,
            custom_configuration=TargetConfiguration(capabilities=_CAPABILITIES),
        )
        self._provider = provider
        self._model_id = model_id
        self._timeout = timeout
        self._label = label
        self._request_log = Path(request_log) if request_log else None
        self._log_lock = threading.Lock()
        self._turn = 0

    @staticmethod
    def _split_model(model: str, provider: str | None) -> tuple[str, str]:
        if provider:
            return provider, model
        head, separator, tail = model.partition("/")
        if separator and head in _KNOWN_PROVIDERS:
            return head, tail
        return "opencode-go", model

    @staticmethod
    def _flatten(conversation: list[Message]) -> str:
        blocks: list[str] = []
        for message in conversation:
            for piece in message.message_pieces:
                role = _ROLE_LABELS.get(piece.role, piece.role.upper())
                if piece.converted_value_data_type == "text":
                    value = piece.converted_value or piece.original_value or ""
                else:
                    value = f"[{piece.converted_value_data_type} piece omitted by adapter]"
                blocks.append(f"[{role}]\n{value}")
        return "\n\n".join(blocks)

    def _append_log(self, record: dict[str, Any]) -> None:
        if self._request_log is None:
            return
        line = json.dumps(record, ensure_ascii=False)
        with self._log_lock:
            self._request_log.parent.mkdir(parents=True, exist_ok=True)
            with self._request_log.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")

    async def _send_prompt_to_target_async(self, *, normalized_conversation: list[Message]) -> list[Message]:
        prompt = self._flatten(normalized_conversation)
        current = normalized_conversation[-1]
        started = time.perf_counter()
        try:
            response_text = await asyncio.to_thread(
                rt_send.send_to_target,
                self._provider,
                self._model_id,
                prompt,
                self._timeout,
            )
        except Exception as error:  # transport failures become evidence, not crashes
            response_text = f"[libertarium target error: {type(error).__name__}: {error}]"
        latency_ms = int((time.perf_counter() - started) * 1000)

        self._turn += 1
        self._append_log(
            {
                "ts": datetime.now(timezone.utc).isoformat(),
                "label": self._label,
                "turn": self._turn,
                "provider": self._provider,
                "model": self._model_id,
                "conversation_id": current.get_piece().conversation_id,
                "prompt": prompt,
                "response": response_text,
                "latency_ms": latency_ms,
            }
        )

        return [
            construct_response_from_request(
                request=current.message_pieces[-1],
                response_text_pieces=[response_text],
            )
        ]
