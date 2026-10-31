"""The wire format the mod's README shows is one the parser reads."""

from pathlib import Path

from hoi4_harness.adapters.logtail import EVENT_CODES, parse_line

README = Path(__file__).resolve().parent.parent / "mod" / "README.md"
DOC = Path(__file__).resolve().parent.parent / "docs" / "mod-bridge.md"


def _examples(path: Path) -> list[str]:
    return [line for line in path.read_text(encoding="utf-8").splitlines() if line.startswith("LLMB|")]


def test_every_documented_line_parses():
    lines = _examples(README) + _examples(DOC)
    assert lines
    for line in lines:
        parsed = parse_line(line)
        assert parsed is not None, line
        kind, fields = parsed
        assert kind in {"state", "boot", "evt"}, line
        if kind == "evt":
            assert fields["kind"] in EVENT_CODES, line
