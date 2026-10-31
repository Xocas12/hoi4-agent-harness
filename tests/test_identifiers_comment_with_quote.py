"""identifiers._uncommented leaves a comment in place when it holds an odd
number of double quotes, and the tokenizer then swallows the following lines
(up to the next quote) as one quoted string."""
from hoi4_harness.identifiers import IdentifierIndex, Playset


def test_comment_with_stray_quote_does_not_hide_later_focuses(tmp_path):
    game = tmp_path / "game"
    (game / "common" / "national_focus").mkdir(parents=True)
    (game / "common" / "national_focus" / "swe.txt").write_text(
        "focus_tree = {\n"
        "\tid = swedish_focus\n"
        "\tcountry = { factor = 0 modifier = { add = 10 tag = SWE } }\n"
        "\tfocus = {\n"
        "\t\tid = SWE_navy  # the \"Sverige class, see wiki\n"
        "\t}\n"
        "\tfocus = {\n"
        "\t\tid = SWE_army\n"
        "\t\tprerequisite = { focus = SWE_navy }\n"
        "\t}\n"
        "\tfocus = { id = SWE_air icon = \"GFX_goal_generic_air\" }\n"
        "}\n",
        encoding="utf-8",
    )
    index = IdentifierIndex.build(Playset.from_paths(game))
    focuses = index.focuses_for("SWE")
    assert {"SWE_navy", "SWE_army", "SWE_air"} <= set(focuses), sorted(focuses)
