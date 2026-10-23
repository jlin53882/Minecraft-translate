"""跨批次、跨種類的同模組 LLM session 狀態。"""

from __future__ import annotations

from dataclasses import dataclass, field

from translation_tool.core.lm_batch_budget import (
    TokenEstimatorConfig,
    estimate_prompt_tokens,
    estimate_text_tokens,
)

DEFAULT_SESSION_HISTORY_TOKEN_LIMIT = 32_000


@dataclass
class TranslationSessionState:
    """Keep one logical conversation per translation operation and module.

    Provider calls remain ordinary stateless HTTP requests; the history is the
    explicit session contract passed to Gemini/Responses. Older turns are rolled
    off before the provider context is exhausted, while the current module and
    its kind-specific payloads remain isolated from other modules.
    """

    key: str
    max_history_tokens: int = DEFAULT_SESSION_HISTORY_TOKEN_LIMIT
    estimator: TokenEstimatorConfig = field(default_factory=TokenEstimatorConfig)
    history: list[dict[str, str]] = field(default_factory=list)
    generation: int = 0

    def snapshot(self) -> list[dict[str, str]]:
        return [dict(turn) for turn in self.history]

    def replace(self, history: list[dict[str, str]]) -> None:
        self.history = [
            {"role": str(turn["role"]), "text": str(turn["text"])}
            for turn in history
            if isinstance(turn, dict)
            and turn.get("role") in {"user", "model"}
            and isinstance(turn.get("text"), str)
        ]
        self._trim()
        self.generation += 1

    def _trim(self) -> None:
        while len(self.history) > 2 and self._token_count() > self.max_history_tokens:
            del self.history[:2]

    def _token_count(self) -> float:
        if self.estimator.algorithm == "tiktoken_calibrated":
            return sum(
                estimate_prompt_tokens(
                    turn.get("text", ""),
                    estimator=self.estimator,
                    include_request_bias=False,
                )
                for turn in self.history
            )
        return sum(estimate_text_tokens(turn.get("text", "")) for turn in self.history)


__all__ = ["TranslationSessionState"]
