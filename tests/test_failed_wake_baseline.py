"""A wake whose model call fails still moves the delta baseline, so the next delta
omits every change the unseen brief carried."""
from hoi4_harness.adapters.mock import MockAdapter
from hoi4_harness.agent.llm.scripted import ScriptedClient
from hoi4_harness.agent.loop import AgentLoop
from hoi4_harness.config import HarnessConfig
from hoi4_harness.env import HOI4Env


class FailsSecondWake(ScriptedClient):
    def __init__(self):
        super().__init__()
        self.wake = 0
        self.briefs = []

    def complete(self, system, messages, tools=None, max_tokens=None):
        if len(messages) == 1:  # first round of a wake
            self.wake += 1
            if self.wake == 2:
                raise TimeoutError("read timed out")
            self.briefs.append(messages[0].text)
        return super().complete(system, messages, tools, max_tokens)


def test_change_shown_only_in_a_failed_wake_reaches_the_model_later(tmp_path):
    config = HarnessConfig(adapter="mock", run_dir=tmp_path, full_brief_every=50)
    adapter = MockAdapter(seed=3)
    env = HOI4Env(adapter, config)
    env.reset()
    client = FailsSecondWake()
    loop = AgentLoop(env=env, planner=client, config=config)
    loop.run(1)
    adapter.state.dockyards += 7  # a change between the seen brief and the failed one
    loop.run(1)  # model call fails before any reply: the model never sees this brief
    loop.run(1)
    assert client.wake == 3
    assert "dockyard" in client.briefs[-1].lower()
