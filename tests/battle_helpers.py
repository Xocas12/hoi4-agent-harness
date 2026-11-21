"""A synthetic text save with divisions in the shape a 1.20 save writes them, and a ``combat`` block.

The grid map is tests/orders_helpers.py: provinces 1..12 row by row, four columns, so province 6 has
the land neighbours 2, 5, 7 and 10, and the states are the columns (10, 11 Swedish; 12, 13 German).
The layout (keys, nesting, ``id`` written twice, ``id={ id= type= }`` references) is what the real
1941 save was checked against; the numbers are invented.
"""

from __future__ import annotations


def division(ident, province, template, *, type_=51, org=50.0, strength=200.0, supply=100.0,
             max_supply=108, oos=None, dig=4.0, dig_cap=8.0, experience=500.0, held=1000,
             missing=0, last_combat="1941.8.1.12", str_damage=None, org_damage=None,
             have=9000, need=10000, acclimatised=None) -> str:
    ref = f"id={{ id={ident} type={type_} }}"
    lines = [
        "\t\t\tdivision={", f"\t\t\t\t{ref}", f"\t\t\t\tlocation={province}", f"\t\t\t\t{ref}",
        f"\t\t\t\tlast_combat_date=\"{last_combat}\"",
        f"\t\t\t\tdivision_template_id={{ id={template} type=52 }}",
        f"\t\t\t\tmax_supply={max_supply}", f"\t\t\t\torganisation={org}", f"\t\t\t\tstrength={strength}",
        "\t\t\t\tequipment={", "\t\t\t\t\tequipment={", "\t\t\t\t\t\tid={ id=5 type=70 }",
        f"\t\t\t\t\t\tamount={held}", "\t\t\t\t\t}", "\t\t\t\t\tallow_zero_entries=no", "\t\t\t\t}",
        "\t\t\t\tarmy_manpower={", "\t\t\t\t\tarmy_manpower_value={",
        f"\t\t\t\t\t\tvalue={{ tag=\"X\" value={have} }}", "\t\t\t\t\t}", "\t\t\t\t\tarmy_manpower_need={",
        f"\t\t\t\t\t\tvalue={{ tag=\"X\" value={need} }}", "\t\t\t\t\t}", "\t\t\t\t}",
        f"\t\t\t\texperience={experience}",
    ]
    if oos is not None:
        lines.append(f"\t\t\t\tout_of_supply_days={oos}")
    if missing:
        lines += ["\t\t\t\trequests={", "\t\t\t\t\treinforcement={", "\t\t\t\t\t\trequest={",
                  f"\t\t\t\t\t\t\tneed={{ infantry_equipment={missing} }}", "\t\t\t\t\t\t}", "\t\t\t\t\t}",
                  "\t\t\t\t}"]
    lines += [f"\t\t\t\tdig_in={dig}", f"\t\t\t\tdig_in_cap={dig_cap}",
              f"\t\t\t\tarmy_current_supply_ratio={supply}"]
    if str_damage is not None:
        lines += [f"\t\t\t\tstr_damage={str_damage}", f"\t\t\t\torg_damage={org_damage or 0}"]
    if acclimatised is not None:
        lines += ["\t\t\t\tacclimatization={", f"\t\t\t\t\tcold_climate={acclimatised}",
                  "\t\t\t\t\tmax_acclimatization=1000", "\t\t\t\t}"]
    lines.append("\t\t\t}")
    return "\n".join(lines) + "\n"


def ref(ident, type_=51) -> str:
    return f"{{ id={ident} type={type_} }}"


def side(units, front, *, tag, weight=70000, reserves=(), tactic=1, taken_str=0.0, taken_org=0.0,
         rate=1.0, rate_str=0.5, recorded=30, air=None, flanked=False) -> str:
    """One side of a land combat. ``units`` / ``front`` / ``reserves`` are (id, type) pairs."""
    body = [f"\t\t\tunit={ref(i, t)}" for i, t in units]
    body += ["\t\t\tlosses=0", "\t\t\tsize={", "\t\t\t\t0 0 0 0", "\t\t\t}",
             "\t\t\tweighted_participants={", "", f"\t\t\t\t{{ \"{tag}\"", f" {weight}}}", "\t\t\t}",
             f"\t\t\tground_damage_str={taken_str}", f"\t\t\tground_damage_org={taken_org}"]
    body += [f"\t\t\tfront={ref(i, t)}" for i, t in front]
    body += [f"\t\t\treserves={ref(i, t)}" for i, t in reserves]
    body += [f"\t\t\ttactic={tactic}", "\t\t\torg_loss_summary={",
             "\t\t\t\t" + " ".join([str(rate)] * 24), "\t\t\t}", "\t\t\tstr_loss_summary={",
             "\t\t\t\t" + " ".join([str(rate_str)] * 24), "\t\t\t}",
             "\t\t\torg_loss_summary_index=5", f"\t\t\tnum_org_losses={recorded}"]
    if air:
        body += ["\t\t\tair_plane={", f"\t\t\t\tamount={air}", "\t\t\t}"]
    if flanked:
        body.append("\t\t\thas_flanked_opponent=yes")
    body += ["\t\t\tlog={", "\t\t\t\tgroup={", "\t\t\t\t\tdamage_dealer=\"X\"", "\t\t\t\t}", "\t\t\t}"]
    return "\n".join(body) + "\n"


def land_combat(ident, province, hours, terrain, attacker: str, defender: str) -> str:
    return (f"\tland_combat={{\n\t\tid={{ id={ident} type=62 }}\n\t\tlocation={province}\n"
            f"\t\tday={hours}\n\t\tduration={hours}\n\t\tattacker={{\n{attacker}\t\t}}\n"
            f"\t\tdefender={{\n{defender}\t\t}}\n\t\tterrain=\"{terrain}\"\n\t}}\n")


def naval_member(tag, sprite="submarine", name="U-1", convoy=False) -> str:
    if convoy:
        return (f"\t\t\t\t\tmember={{\n\t\t\t\t\t\tconvoy={{ id=9 type=4713 }}\n"
                f"\t\t\t\t\t\tcached_info={{ sprite=\"convoy\" tag=\"{tag}\" convoy=yes }}\n\t\t\t\t\t}}\n")
    return (f"\t\t\t\t\tmember={{\n\t\t\t\t\t\tship={{ id=3 type=51 }}\n"
            f"\t\t\t\t\t\tcached_info={{ sprite=\"{sprite}\" tag=\"{tag}\" ship=\"{name}\" }}\n"
            "\t\t\t\t\t}\n")


def naval_combat(ident, province, hours, attacker_members: str, defender_members: str, *,
                 att_dealt=0.0, def_dealt=0.0, att_compare=1.0, def_compare=1.0) -> str:
    def side_(members, dealt, compare):
        return (f"\t\t\tunit={{ id=7 type=61 }}\n\t\t\tgroup={{\n{members}"
                f"\t\t\t\tforces_compare={compare}\n\t\t\t}}\n\t\t\tpositioning=1\n"
                f"\t\t\ttotal_damage_dealt={dealt}\n\t\t\ttotal_initial_strength=100\n")
    return (f"\tnaval_combat={{\n\t\tid={{ id={ident} type=4713 }}\n\t\tlocation={province}\n"
            f"\t\tday=-1\n\t\tduration={hours}\n\t\tattacker={{\n"
            f"{side_(attacker_members, att_dealt, att_compare)}"
            f"\t\t}}\n\t\tdefender={{\n{side_(defender_members, def_dealt, def_compare)}\t\t}}\n"
            "\t\tterrain=\"water_shallow_sea\"\n\t}\n")


TEMPLATES = ((1, "Infantry", "SWE"), (2, "Cavalry", "SWE"), (3, "Infanterie", "GER"),
             (4, "Panzer", "GER"), (5, "Infanterie FR", "FRA"))


def save_text(swe_units: str, ger_units: str, combat: str = "", controllers=None,
              swe_at_war_with=("GER",), date="1941.8.31.12") -> str:
    states = "".join(
        f"\t{sid}={{\n\t\towner=\"{owner}\"\n\t}}\n"
        for sid, owner in ((10, "SWE"), (11, "SWE"), (12, "GER"), (13, "GER")))
    provinces = "".join(f"\t{p}={{\n\t\tcontroller=\"{c}\"\n\t}}\n" for p, c in (controllers or {}).items())
    wars = "".join(f"\t\t\t\t{t}={{\n\t\t\t\t\twar_relation={{\n\t\t\t\t\t\tfirst=\"SWE\"\n"
                   f"\t\t\t\t\t\tsecond=\"{t}\"\n\t\t\t\t\t}}\n\t\t\t\t}}\n" for t in swe_at_war_with)
    templates = "".join(f'\tdivision_template={{\n\t\tid={{ id={i} type=52 }}\n\t\tname="{n}"\n'
                        f'\t\tcountry="{c}"\n\t}}\n' for i, n, c in TEMPLATES)
    return (
        f'HOI4txt\ndate="{date}"\nplayer="SWE"\n'
        "equipments={\n\tinfantry_equipment_1={\n\t\tid={ id=5 type=70 }\n\t}\n}\n"
        f"states={{\n{states}}}\nprovinces={{\n{provinces}}}\n"
        f"division_templates={{\n{templates}}}\n"
        "countries={\n"
        f"\tSWE={{\n\t\tdiplomacy={{\n\t\t\tactive_relations={{\n{wars}\t\t\t}}\n\t\t}}\n"
        f"\t\tunits={{\n{swe_units}\t\t}}\n\t}}\n"
        f"\tGER={{\n\t\tunits={{\n{ger_units}\t\t}}\n\t}}\n"
        "}\n"
        + (f"combat={{\n{combat}}}\n" if combat else ""))
