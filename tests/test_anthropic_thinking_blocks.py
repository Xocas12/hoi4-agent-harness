"""AnthropicClient.complete runs with thinking={"type":"adaptive"}, but keeps only
text and tool_use blocks; the loop then sends the assistant turn back for the
next tool round without its thinking block(s). Anthropic requires the thinking
blocks of the assistant turn that issued the tool_use to be passed back
unchanged alongside the tool_result."""
from types import SimpleNamespace as NS

from hoi4_harness.agent.llm.anthropic_provider import AnthropicClient, wire_messages
from hoi4_harness.agent.llm.base import Msg, ToolResult


class FakeStream:
    def __init__(self, message):
        self.message = message
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False
    def get_final_message(self):
        return self.message


def test_thinking_block_survives_the_tool_round_trip():
    message = NS(
        content=[
            NS(type="thinking", thinking="", signature="sig-abc"),
            NS(type="tool_use", id="tu_1", name="advance_time", input={"days": 7}),
        ],
        stop_reason="tool_use",
        usage=NS(input_tokens=10, output_tokens=5, cache_read_input_tokens=0,
                 cache_creation_input_tokens=0),
    )
    client = AnthropicClient.__new__(AnthropicClient)
    client.client = NS(messages=NS(stream=lambda **kw: FakeStream(message)))
    client.model, client.max_tokens, client.effort, client.cache_prefix = "m", 100, "low", False
    response = client.complete("sys", [Msg(role="user", text="brief")], tools=None)
    # What loop.py appends before the next round:
    history = [Msg(role="user", text="brief"),
               Msg(role="assistant", text=response.text, tool_calls=response.tool_calls,
                   provider_blocks=response.provider_blocks),
               Msg(role="user", tool_results=[ToolResult("tu_1", "ok", name="advance_time")])]
    wire = wire_messages(history)
    assistant = wire[1]["content"]
    assert assistant[0]["type"] == "thinking", assistant
    assert assistant[0]["signature"] == "sig-abc"
    assert assistant[1]["type"] == "tool_use"


def test_the_loop_sends_the_blocks_back_on_the_next_round(tmp_path):
    from hoi4_harness.adapters.mock import MockAdapter
    from hoi4_harness.agent.llm.base import LLMResponse, ToolCall
    from hoi4_harness.agent.llm.scripted import ScriptedClient
    from hoi4_harness.agent.loop import AgentLoop
    from hoi4_harness.config import HarnessConfig
    from hoi4_harness.env import HOI4Env

    block = {"type": "thinking", "thinking": "", "signature": "sig-abc"}
    seen = []

    class Thinking(ScriptedClient):
        def complete(self, system, messages, tools=None, max_tokens=None):
            seen.append(list(messages))
            if len(seen) == 1:
                return LLMResponse(tool_calls=[ToolCall("c1", "note", {"text": "x"})],
                                   stop_reason="tool_use", provider_blocks=[block])
            return LLMResponse(tool_calls=[ToolCall("c2", "advance_time", {"days": 7})],
                               stop_reason="tool_use")

    config = HarnessConfig(adapter="mock", run_dir=tmp_path)
    env = HOI4Env(MockAdapter(seed=3), config)
    env.reset()
    AgentLoop(env=env, planner=Thinking(), config=config).run(1)
    assistant = [m for m in seen[1] if m.role == "assistant"]
    assert assistant and assistant[0].provider_blocks == [block]
