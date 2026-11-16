# SWE text-save fixture

`swe_1936.hoi4` is a minimized structural extract of the local, non-ironman
Operation Postern v1.19.5.0.7c6c (238a) save, `SWE_1936_01_01_12.hoi4`.
It is reformatted for readability; it is a parser fixture, not a loadable game.

Retained data:

- All 13 SWE-owned states: owner, buildings and manpower pools. These preserve
  the verified totals of 31,153 available manpower and 9/3/3 factory levels.
- Political power, raw stability/war support and the ambiguous manpower ratio.
- Three empty research slots, and both military and all four naval lines.
  Factory-efficiency arrays keep only their first entry; efficiencies are not mapped.
- All 12 division template references and the four SWE template definitions.
- The equipment definitions referenced by the production lines.
- The actual anonymous `resources.to_use` blocks, including an empty block.
- A reduced GER occupation/CAN/state_garrison_data/332 hierarchy with the same
  unindented nested keys that broke the old column-zero heuristic.

Unrelated countries, technologies, histories, resources, equipment variants,
units, identifiers and cached bridge variables have been removed. The original
49.9 MB file (49.3 million decoded characters) stays outside the repository.
The fixture contains no user paths or session/account identifiers.
