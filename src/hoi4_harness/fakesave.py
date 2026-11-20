"""The text save the fake game writes when the console says ``savegame``.

Same shape as the one the real game wrote on 1.20.0 (``tests/fixtures/
swe_1938_02_04.hoi4`` is a trimmed copy of it): the header, ``states`` with their
buildings and owners, the ``equipments`` the production lines refer to, the
player's ``division_templates``, and ``countries`` with the player's block cut
to what ``adapters/savegame.py`` reads -- politics, the mod's variables, the
production lines and the construction queue (``production.general_lines``), the
focus, the divisions and the research slots. A test reads both through the same
mapping, so a layout the fake gets wrong shows up as a mismatch with the real
file, not as a green run against a made-up format.

Nothing here is the game's own data; it exists so the save-reading half of the
headless stack can be tested with no game.
"""

from __future__ import annotations

from .types import GameState

#: Points one level of each building costs (the real queue's ``cost``).
BUILDING_COST = {
    "civilian_factory": 10800, "military_factory": 7200, "dockyard": 7200, "infrastructure": 3000,
    "air_base": 1000, "anti_air": 1000, "radar": 1000, "synthetic_refinery": 7200,
}
BUILDING_TEMPLATE = {
    "civilian_factory": "industrial_complex", "military_factory": "arms_factory",
    "dockyard": "dockyard", "infrastructure": "infrastructure", "air_base": "air_base",
    "anti_air": "anti_air_building", "radar": "radar_station",
    "synthetic_refinery": "synthetic_refinery",
}
FIRST_EQUIPMENT_ID = 1326
FIRST_TEMPLATE_ID = 21


def _indent(depth: int) -> str:
    return "\t" * depth


def _spread(total: int, parts: int) -> list[int]:
    """``total`` split as evenly as possible over ``parts`` (the remainder first)."""
    if parts <= 0:
        return []
    base, extra = divmod(max(total, 0), parts)
    return [base + (1 if i < extra else 0) for i in range(parts)]


def render_save(game, hour: int) -> str:
    """The text of a save of ``game`` (a ``FakeGame``) as of the end of today."""
    world, state, tag = game.world, game.world.state, game.country
    date = world.date
    out: list[str] = [
        "HOI4txt\n",
        f'player="{tag}"\nideology={state.ideology}\n',
        f'date="{date.year}.{date.month}.{date.day}.{hour}"\n',
        'difficulty="normal"\nversion="Fake HOI4 (hoi4-harness fakegame)"\ntutorial=no\n',
        f'player_countries={{\n\t{tag}={{\n\t\tuser="player"\n\t\tcountry_leader=yes\n\t\tid=1\n\t}}\n}}\n',
        'save_version=33\nmods={\n\t"LLM Bridge"\n}\n',
        'gameplaysettings={\n\tdifficulty="normal"\n\tironman=0\n'
        f'\thistorical={int(getattr(game, "historical", 0))}\n}}\n',
        'start_date="1936.1.1.12"\n',
    ]

    # --- states -------------------------------------------------------------
    owned = [s for s in game.states.values() if s.owner == tag]
    civ = _spread(state.civilian_factories, len(owned))
    mil = _spread(state.military_factories, len(owned))
    dock = _spread(state.dockyards, len(owned))
    pool = _spread(state.manpower, len(owned))
    shares = {s.id: (civ[i], mil[i], dock[i], pool[i]) for i, s in enumerate(owned)}
    out.append("states={\n")
    for s in game.states.values():
        c, m, d, p = shares.get(s.id, (1, 0, 0, 0))
        out.append(f"\t{s.id}={{\n\t\tbuildings={{\n")
        out.append("\t\t\tinfrastructure={\n\t\t\t\tlevel=2\n\t\t\t\tpartial_health=0\n\t\t\t\thealthy_levels=2\n\t\t\t}\n")
        for name, level in (("industrial_complex", c), ("arms_factory", m), ("dockyard", d)):
            if level:
                out.append(f"\t\t\t{name}={{\n\t\t\t\tlevel={level}\n\t\t\t\tpartial_health=0\n"
                           f"\t\t\t\thealthy_levels={level}\n\t\t\t}}\n")
        out.append(f'\t\t}}\n\t\towner="{s.owner}"\n')
        out.append(f"\t\tmanpower_pool={{\n\t\t\tavailable={p}\n\t\t\tlocked=0\n\t\t\ttotal={p}\n\t\t}}\n")
        out.append("\t}\n")
    out.append("}\n")

    # --- equipment variants and division templates ----------------------------
    equipment_ids: dict[str, int] = {}
    for line in state.production:
        equipment_ids.setdefault(line.equipment, FIRST_EQUIPMENT_ID + len(equipment_ids))
    out.append("equipments={\n")
    for name, ident in equipment_ids.items():
        out.append(f'\t{name}={{\n\t\tid={{ id={ident} type=70 }}\n\t\tmax_version=0\n'
                   f'\t\tis_frame=no\n\t\tcreator="{tag}"\n\t\torigin="---"\n\t}}\n')
    out.append("}\n")
    template_ids = {g.template: FIRST_TEMPLATE_ID + i for i, g in enumerate(state.divisions)}
    out.append("division_templates={\n")
    for name, ident in template_ids.items():
        out.append(f'\tdivision_template={{\n\t\tid={{ id={ident} type=52 }}\n\t\tname="{name}"\n'
                   f'\t\tcountry="{tag}"\n\t}}\n')
    out.append("}\n")

    # --- the player's country ---------------------------------------------------
    out.append("countries={\n")
    out.append(f"\t{tag}={{\n")
    out.append(f"\t\tpolitics={{\n\t\t\tpolitical_power={state.political_power:.5f}\n"
               f"\t\t\truling_party={state.ideology}\n\t\t}}\n")
    out.append(f"\t\tvariables={{\n\t\t\tllmb_stab={state.stability * 100:.3f}\n"
               f"\t\t\tllmb_ws={state.war_support * 100:.0f}\n\t\t}}\n")
    out.append(_production(state, equipment_ids, game))
    out.append(_focus(state, world))
    out.append(_units(state, template_ids, tag))
    out.append(_technology(state))
    out.append(f"\t\tfuel_status={{\n\t\t\tfuel={state.fuel:.5f}\n\t\t\tmax_fuel=76875.00000\n\t\t}}\n")
    out.append("\t\tcapital=141\n\t\tresearch_slot=3\n")
    out.append("\t}\n}\n")
    return "".join(out)


def _production(state: GameState, equipment_ids: dict[str, int], game) -> str:
    lines = ["\t\tproduction={\n"]
    for number, line in enumerate(state.production):
        ident = equipment_ids[line.equipment]
        lines.append(f"\t\t\tmilitary_lines={{\n\t\t\t\tid={{ id={600 + number} type=56 }}\n")
        if line.output_per_day == 0 and line.efficiency == 0:
            # A line that has only asked for factories, as add_equipment_production
            # leaves it: no active factories, nothing produced.
            lines.append(f"\t\t\t\tqueued_factories={line.factories}\n")
        else:
            lines.append(f"\t\t\t\tproduced={line.output_per_day * 10:.5f}\n"
                         f"\t\t\t\tactive_factories={line.factories}\n")
        lines.append(f"\t\t\t\tpriority={number}\n\t\t\t\tamount=-1\n")
        if line.output_per_day:
            lines.append(f"\t\t\t\tspeed={line.output_per_day:.5f}\n")
        lines.append(f"\t\t\t\tcost=1\n\t\t\t\trequested_factories={line.factories}\n"
                     f"\t\t\t\tequipment_variant_index={{ id={ident} type=70 }}\n")
        if line.efficiency:
            values = " ".join(f"{line.efficiency * 100:.5f}" for _ in range(line.factories))
            lines.append(f"\t\t\t\tfactory_efficiencies={{\n\t\t\t\t\t{values}\n\t\t\t\t}}\n")
        lines.append("\t\t\t}\n")
    if state.construction:
        lines.append("\t\t\tgeneral_lines={\n")
        ids = {s.name: s.id for s in game.states.values()}
        for number, item in enumerate(state.construction):
            cost = BUILDING_COST.get(item.building, 5000)
            done = item.progress * cost
            speed = (cost - done) / item.days_remaining if item.days_remaining else 100.0
            lines.append(
                f"\t\t\t\tbuilding={{\n\t\t\t\t\tid={{ id={1273 + number} type=59 }}\n"
                f"\t\t\t\t\tproduced={done:.1f}\n\t\t\t\t\tactive_factories=5\n\t\t\t\t\tpriority=0\n"
                f"\t\t\t\t\tamount=1\n\t\t\t\t\tspeed={speed:.2f}\n\t\t\t\t\tcost={cost}\n"
                f'\t\t\t\t\tcreated_date="1936.1.1.12"\n\t\t\t\t\tbuilding={{\n'
                f"\t\t\t\t\t\ttemplate={BUILDING_TEMPLATE.get(item.building, item.building)}\n"
                f"\t\t\t\t\t\tlocation={ids.get(item.state, item.state)}\n\t\t\t\t\t}}\n"
                "\t\t\t\t\tallied_build=no\n\t\t\t\t}\n")
        lines.append("\t\t\t}\n")
    lines.append("\t\t}\n")
    return "".join(lines)


def _focus(state: GameState, world) -> str:
    done = "".join(f"\t\t\tcompleted={f}\n" for f in state.completed_focuses)
    current = f"\t\t\tcurrent={state.national_focus}\n" if state.national_focus else ""
    if not done and not current:
        return ""
    return f"\t\tfocus={{\n{done}\t\t\tprogress=24\n{current}\t\t\tpaused=no\n\t\t}}\n"


def _units(state: GameState, template_ids: dict[str, int], tag: str) -> str:
    out = ["\t\tunits={\n"]
    unit = 604
    for group in state.divisions:
        for _ in range(group.count):
            out.append(f"\t\t\tdivision={{\n\t\t\t\tid={{ id={unit} type=51 }}\n"
                       f'\t\t\t\tlogical_country="{tag}"\n'
                       f"\t\t\t\tdivision_template_id={{ id={template_ids[group.template]} type=52 }}\n"
                       "\t\t\t\tstrength=154.2\n\t\t\t}\n")
            unit += 1
    out.append("\t\t}\n")
    return "".join(out)


def _technology(state: GameState) -> str:
    out = ["\t\ttechnology={\n\t\t\tslots={\n"]
    for slot in state.research:
        if slot.technology:
            out.append(f"\t\t\t\t{slot.technology}={{\n\t\t\t\t\tused_saved_points=30\n\t\t\t\t}}\n")
        else:
            out.append("\t\t\t\tempty={\n\t\t\t\t\tpoints=30\n\t\t\t\t}\n")
    out.append("\t\t\t}\n\t\t}\n")
    return "".join(out)
