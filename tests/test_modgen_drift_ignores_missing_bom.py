"""modgen.drift reads every file with utf-8-sig, so a localisation file that
lost its BOM (which HOI4 then refuses to load) is not reported as drift."""
from hoi4_harness import modgen


def test_localisation_without_bom_is_drift(tmp_path):
    modgen.write(tmp_path)
    loc = tmp_path / "localisation/english/llm_bridge_targets_l_english.yml"
    raw = loc.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")
    loc.write_bytes(raw[3:])
    assert "localisation/english/llm_bridge_targets_l_english.yml" in modgen.drift(tmp_path)
