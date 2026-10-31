"""country_block matches the tag at one OR two levels of indentation, so in a
tab-indented save it can return another country's nested block keyed by the
player's tag instead of the player's own country block."""
from hoi4_harness.adapters.savegame import country_block

SAVE = (
    'date="1936.1.1.12"\n'
    'player="SWE"\n'
    "countries={\n"
    "\tGER={\n"
    "\t\tSWE={\n"
    "\t\t\topinion=10\n"
    "\t\t}\n"
    "\t\tpolitical_power=5\n"
    "\t}\n"
    "\tSWE={\n"
    "\t\tpolitical_power=99\n"
    "\t}\n"
    "}\n"
)


def test_player_block_is_the_top_level_one():
    block = country_block(SAVE, "SWE")
    assert block is not None and block.get("political_power") == "99", block
