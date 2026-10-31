"""Focus trees are assigned to countries only through `tag = X` inside the
tree's `country` block; trees keyed with `original_tag = X` (the usual form in
total conversions such as Kaiserreich) are never assigned, so the country falls
back to the generic tree and every real focus of its own is refused."""
from hoi4_harness.identifiers import IdentifierIndex, Playset
from hoi4_harness.types import ActionCall


def test_tree_keyed_on_original_tag_belongs_to_that_country(tmp_path):
    game = tmp_path / "game"
    (game / "common" / "national_focus").mkdir(parents=True)
    (game / "common" / "national_focus" / "generic.txt").write_text(
        "focus_tree = { id = generic_focus default = yes country = { factor = 1 }\n"
        "  focus = { id = generic_army } }\n")
    (game / "common" / "national_focus" / "ger.txt").write_text(
        "focus_tree = { id = german_focus\n"
        "  country = { factor = 0 modifier = { add = 10 original_tag = GER } }\n"
        "  focus = { id = GER_rhineland } }\n")
    index = IdentifierIndex.build(Playset.from_paths(game))
    call = ActionCall(name="set_national_focus", arguments={"focus_id": "GER_rhineland"})
    assert index.check(call, "GER") is None, index.check(call, "GER")
