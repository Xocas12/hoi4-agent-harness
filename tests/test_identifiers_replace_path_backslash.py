"""Mod.load strips '/' before converting backslashes, so a Windows-style
replace_path with a trailing backslash keeps a trailing '/' and never matches."""
from hoi4_harness.identifiers import IdentifierIndex, Mod, Playset


def test_windows_replace_path_with_trailing_backslash(tmp_path):
    game = tmp_path / "game"
    (game / "common" / "country_tags").mkdir(parents=True)
    (game / "common" / "country_tags" / "00_countries.txt").write_text("GER = x\n")
    mod = tmp_path / "mod"
    (mod / "common" / "country_tags").mkdir(parents=True)
    (mod / "common" / "country_tags" / "tc.txt").write_text("KAI = x\n")
    (mod / "descriptor.mod").write_text(
        'name = "TC"\nreplace_path = "common\\country_tags\\"\n')
    assert Mod.load(mod).replace_paths == ["common/country_tags"]
    index = IdentifierIndex.build(Playset.from_paths(game, [mod]))
    assert index.tags == {"KAI"}
