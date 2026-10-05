import pytest

from app.graph.rag_graph import check_output_guardrail


@pytest.mark.asyncio
async def test_check_output_guardrail_redacts_leaked_secret_from_response():
    state = {"response": "Here is the key: sk-abcdefghijklmnopqrstuvwxyz0123456789ABCD"}

    result = await check_output_guardrail(state, {})

    assert "sk-abcdefghijklmnopqrstuvwxyz0123456789ABCD" not in result["response"]
    assert result["guardrail_flags"]


@pytest.mark.asyncio
async def test_check_output_guardrail_leaves_clean_response_unchanged():
    state = {"response": "Revenue grew 12% in Q3."}

    result = await check_output_guardrail(state, {})

    assert result["response"] == "Revenue grew 12% in Q3."
    assert result["guardrail_flags"] == []
