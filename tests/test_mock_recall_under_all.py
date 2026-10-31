"""With 'all' delegated, recalling one named army changes nothing in the mock
yet is reported ok -- a silent no-op reported as success ('refuse, don't fake')."""
from hoi4_harness.adapters.mock import MockAdapter
from hoi4_harness.types import ActionCall


def test_recall_that_changes_nothing_is_not_ok():
    m = MockAdapter()
    m.apply(ActionCall(name="delegate_army_to_ai", arguments={"army": "all", "delegate": True}))
    result = m.apply(ActionCall(name="delegate_army_to_ai",
                                arguments={"army": "1st Army", "delegate": False}))
    still = m.read_state().delegated_armies
    assert not (result.ok and still == ["all"]), (result, still)
