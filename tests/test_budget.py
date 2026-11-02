import pytest

from enterprise_context.budget import ContextBudgeter, TokenBudgetExceeded
from enterprise_context.schemas import ContextPayload


def payload(**updates):
    data = dict(user_id="u1", session_id="s1", tenant_id="t1",
                system_prompt="Never disclose confidential data.",
                static_context=["Static policy: use AES-256."],
                dynamic_context=["A very long context block. " * 100],
                history=["old message " * 40, "recent message " * 30],
                query="What encryption should we use?")
    data.update(updates)
    return ContextPayload(**data)


def test_budget_prunes_context_and_keeps_anchors():
    compiled = ContextBudgeter().compile(payload(), 160)
    assert "Never disclose confidential data." in compiled.system_prefix
    assert "USER QUERY:\nWhat encryption should we use?" in compiled.user_prompt
    assert compiled.estimated_tokens <= 160
    assert compiled.pruned_dynamic_chunks > 0 or compiled.dropped_history_messages > 0


def test_mandatory_content_cannot_exceed_budget():
    with pytest.raises(TokenBudgetExceeded):
        ContextBudgeter().compile(payload(system_prompt="x" * 2000, query="y" * 1000), 128)
