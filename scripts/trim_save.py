"""Cut a real HOI4 text save down to a fixture the tests can commit.

    python scripts/trim_save.py "save games/SWE_1938_02_04_22.hoi4" \
        tests/fixtures/swe_1938_02_04.hoi4

A save is 60+ MB; the harness reads a handful of blocks from it. This keeps
exactly those, in the layout the game wrote them in, and scrubs what identifies
a person or a game: the account name becomes "player", and seeds, unique ids and
session numbers are dropped. Kept:

* the header (player, date, version, mods) and ``start_date``;
* the ``states`` the player owns, plus two foreign ones;
* only the ``equipments`` the player's production lines refer to;
* the player's ``division_templates``;
* ``countries``: the player's block cut to the keys the mapping reads (politics,
  variables, production, focus, units, technology slots, fuel...), and the two
  decoys a real save puts in front of naive readers (a block at column 0 inside
  ENG, a ``SWE={ opinion }`` nested in GER).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from hoi4_harness.adapters.savegame import matching_brace  # noqa: E402

#: Country-block keys (two tabs deep) the mapping reads, or the tests look at.
KEEP_COUNTRY = ("politics", "variables", "production", "focus", "units", "technology", "fuel_status",
                "capital", "stability", "war_support", "manpower", "research_slot", "focus_tree",
                "flags")


def decoys(player: str) -> str:
    """The two traps a real save puts in front of naive readers, for any player tag.

    The block that holds a nested ``{player}={ opinion }`` must be a country other
    than the player: with the player's own tag it becomes a second, wrong player
    block (this planted ``politics=999`` ahead of the real Germany in a GER save).
    """
    host = "GER" if player != "GER" else "FRA"
    other = "ENG" if player != "ENG" else "ITA"
    return f"""	{other}={{
		politics={{
			political_power=12.5
		}}
CAN={{
		autonomous=yes
}}
		stability=0.9
	}}
	{host}={{
		{player}={{
			opinion=10
		}}
		politics={{
			political_power=999
		}}
	}}
"""


def blocks(text: str, start: int, indent: str) -> list[tuple[str, int, int]]:
    """The ``key={...}`` entries at ``indent`` inside the block opened at ``start``."""
    end = matching_brace(text, start)
    found = []
    pattern = re.compile(rf"^{indent}([A-Za-z0-9_]+)=\{{", re.M)
    pos = start + 1
    while True:
        match = pattern.search(text, pos, end)
        if not match:
            return found
        stop = matching_brace(text, match.end() - 1)
        found.append((match.group(1), match.start(), stop))
        pos = stop


def top(text: str, key: str) -> tuple[int, int]:
    match = re.search(rf"^{key}=\{{", text, re.M)
    return match.end() - 1, matching_brace(text, match.end() - 1)


def trim(text: str) -> str:
    player = re.search(r'^player="([A-Z0-9]{3})"', text, re.M).group(1)
    head = text[: text.index("save_version=")]
    head = re.sub(r'user="[^"]*"', 'user="player"', head)
    out = [head, "save_version=33\nmods={\n\t\"LLM Bridge\"\n}\n",
           re.search(r"^start_date=.*\n", text, re.M).group(0)]

    country = re.search(rf"^\t{player}=\{{", text[text.index("\ncountries={"):], re.M)
    c_start = text.index("\ncountries={") + country.start()
    c_open = text.index("{", c_start)
    parts = blocks(text, c_open, "\t\t")
    kept = []
    for key, a, b in parts:
        if key not in KEEP_COUNTRY:
            continue
        chunk = text[a:b]
        if key == "technology":
            slots = blocks(text, text.index("{", a), "\t\t\t")
            chunk = "\t\ttechnology={\n" + "".join(
                text[sa:sb] + "\n" for k, sa, sb in slots if k == "slots") + "\t\t}"
        if key == "production":
            sub = blocks(text, text.index("{", a), "\t\t\t")
            lines = [text[sa:sb] for k, sa, sb in sub if k in ("military_lines", "general_lines")]
            chunk = "\t\tproduction={\n" + "\n".join(lines) + "\n\t\t}"
        if key == "fuel_status":
            chunk = "\t\tfuel_status={\n" + "\n".join(
                m for m in re.findall(r"^\t\t\t(?:fuel|max_fuel)=.*$", chunk, re.M)) + "\n\t\t}"
        kept.append(chunk)
    # Scalar keys at two tabs (stability, war_support, capital...).
    body = text[c_open:matching_brace(text, c_open)]
    for scalar in re.findall(rf"^\t\t(?:{'|'.join(KEEP_COUNTRY)})=[^{{\n]*$", body, re.M):
        kept.append(scalar)
    player_block = f"\t{player}={{\n" + "\n".join(
        k if k.startswith("\t\t") else "\t\t" + k for k in kept) + "\n\t}\n"

    # states: the player's, plus two foreign.
    s_open, s_end = top(text, "states")
    states = [(k, a, b) for k, a, b in blocks(text, s_open, "\t") if k.isdigit()]
    keep_states = [text[a:b] for k, a, b in states
                   if f'owner="{player}"' in text[a:b] or k in ("39", "40")]
    out.append("states={\n" + "\n".join(keep_states) + "\n}\n")

    # equipment variants the player's lines use.
    wanted = set(re.findall(r"equipment_variant_index=\{ id=(\d+) ", player_block))
    e_open, e_end = top(text, "equipments")
    eq = [text[a:b] for k, a, b in blocks(text, e_open, "\t")
          if re.search(r"id=\{ id=(\d+) ", text[a:b]).group(1) in wanted]
    out.append("equipments={\n" + "\n".join(eq) + "\n}\n")

    # the player's division templates.
    d_open, d_end = top(text, "division_templates")
    dts = [text[a:b] for k, a, b in blocks(text, d_open, "\t") if f'country="{player}"' in text[a:b]]
    out.append("division_templates={\n" + "\n".join(dts) + "\n}\n")

    out.append("countries={\n" + decoys(player) + player_block + "}\n")
    # Seeds identify a game, not a position in it.
    pattern = r"^[ \t]*(?:seed|random|[a-z_]*random_seed|game_unique_\w+)=.*\n"
    return re.sub(pattern, "", "".join(out), flags=re.M)


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__)
        return 2
    text = Path(argv[1]).read_text(encoding="utf-8", errors="replace")
    result = trim(text)
    Path(argv[2]).write_text(result, encoding="utf-8", newline="\n")
    print(f"{argv[2]}: {len(result) / 1000:.0f} kB from {len(text) / 1e6:.0f} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
