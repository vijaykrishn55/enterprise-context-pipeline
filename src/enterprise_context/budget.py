"""Bounded context compiler that preserves mandatory system and query anchors."""
from dataclasses import dataclass

from .schemas import ContextPayload


class TokenBudgetExceeded(ValueError):
    """Mandatory system prompt and query cannot fit the configured token budget."""


def estimate_tokens(text: str) -> int:
    """Conservative portable estimate; production deployments can inject a model tokenizer."""
    return max(1, (len(text) + 3) // 4) if text else 0


@dataclass(frozen=True)
class CompiledContext:
    system_prefix: str
    user_prompt: str
    estimated_tokens: int
    pruned_dynamic_chunks: int
    dropped_history_messages: int
    audit: tuple[str, ...]


class ContextBudgeter:
    def compile(self, payload: ContextPayload, maximum: int) -> CompiledContext:
        base_cost = estimate_tokens(payload.system_prompt) + estimate_tokens(payload.query) + 12
        if base_cost > maximum:
            raise TokenBudgetExceeded(
                f"Mandatory system prompt and query require about {base_cost} tokens; budget is {maximum}."
            )
        remaining = maximum - base_cost
        audit: list[str] = []
        static_kept, remaining, static_clipped = self._fit_blocks(payload.static_context, remaining)
        dynamic_kept, remaining, dynamic_clipped = self._fit_blocks(payload.dynamic_context, remaining)
        history_kept, remaining, history_clipped = self._fit_recent(payload.history, remaining)
        if static_clipped:
            audit.append(f"static_context_blocks_pruned_or_truncated={static_clipped}")
        if dynamic_clipped:
            audit.append(f"dynamic_context_blocks_pruned_or_truncated={dynamic_clipped}")
        if history_clipped:
            audit.append(f"history_messages_dropped_or_truncated={history_clipped}")

        static_text = "\n".join(static_kept)
        dynamic_text = "\n".join(dynamic_kept)
        history_text = "\n".join(history_kept)
        system_prefix = payload.system_prompt
        if static_text:
            system_prefix += "\n\nSTATIC CONTEXT (prefix-aligned):\n" + static_text
        sections = []
        if dynamic_text:
            sections.append("DYNAMIC CONTEXT:\n" + dynamic_text)
        if history_text:
            sections.append("RECENT HISTORY:\n" + history_text)
        sections.append("USER QUERY:\n" + payload.query)
        user_prompt = "\n\n".join(sections)
        actual_estimate = estimate_tokens(system_prefix) + estimate_tokens(user_prompt)
        # Account for formatting overhead in the compiled representation.
        if actual_estimate > maximum:
            user_prompt = "USER QUERY:\n" + payload.query
            actual_estimate = estimate_tokens(system_prefix) + estimate_tokens(user_prompt)
        if actual_estimate > maximum:
            raise TokenBudgetExceeded("Compiled context exceeded the token budget after pruning.")
        return CompiledContext(
            system_prefix=system_prefix,
            user_prompt=user_prompt,
            estimated_tokens=actual_estimate,
            pruned_dynamic_chunks=dynamic_clipped,
            dropped_history_messages=history_clipped,
            audit=tuple(audit),
        )

    @staticmethod
    def _fit_blocks(blocks: list[str], remaining: int) -> tuple[list[str], int, int]:
        kept: list[str] = []
        pruned = 0
        for block in blocks:
            cost = estimate_tokens(block)
            if cost <= remaining:
                kept.append(block)
                remaining -= cost
            elif remaining >= 8:
                clipped = block[:remaining * 4]
                kept.append(clipped)
                remaining -= estimate_tokens(clipped)
                pruned += 1
                break
            else:
                pruned += 1
        return kept, remaining, pruned

    @staticmethod
    def _fit_recent(history: list[str], remaining: int) -> tuple[list[str], int, int]:
        kept_reverse: list[str] = []
        dropped = 0
        for item in reversed(history):
            cost = estimate_tokens(item)
            if cost <= remaining:
                kept_reverse.append(item)
                remaining -= cost
            elif remaining >= 8:
                kept_reverse.append(item[:remaining * 4])
                remaining = 0
                dropped += 1
                break
            else:
                dropped += 1
        return list(reversed(kept_reverse)), remaining, dropped
