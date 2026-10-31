"""docs/customization.md: 'an unknown key is an error rather than a silent no-op'.
Nested [budget]/[planner] keys are setattr'd blindly, so a typo silently drops a cap."""
import pytest

from hoi4_harness.config import HarnessConfig


def test_typo_in_budget_table_is_an_error():
    config = HarnessConfig()
    with pytest.raises(KeyError):
        config.merge({"budget": {"max_dollars": 5.0}})   # meant max_usd


def test_typo_in_planner_table_is_an_error():
    config = HarnessConfig()
    with pytest.raises(KeyError):
        config.merge({"planner": {"max_token": 2000}})   # meant max_tokens
