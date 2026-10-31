"""The docs name the catalog's size and list every verb; both follow the catalog."""
from pathlib import Path

from hoi4_harness.actions.catalog import ACTIONS

ROOT = Path(__file__).resolve().parents[1]


def test_action_schema_lists_every_action():
    doc = (ROOT / "docs" / "action-schema.md").read_text(encoding="utf-8")
    assert f"{len(ACTIONS)} verbs" in doc
    for action in ACTIONS:
        assert f"| `{action.name}` |" in doc, action.name


def test_readme_counts_the_actions():
    assert f"({len(ACTIONS)} actions)" in (ROOT / "README.md").read_text(encoding="utf-8")
