"""One hour of one battle: the math, kept free of map and movement so it can be checked by hand.

Each side shoots at the other at the same time. For a side with total attack A against targets
with total defense D (and, for an attacker, breakthrough B):

    effective defense   D' = max(D - B, 0.25 D)           breakthrough chews through defense
    attempts            n  = round(A * hit_rate)          stochastic rounding
    chance per attempt  p  = min(1, hit_pass_scale * A / (A + D'))
    each hit lands on a random enemy division and does
        strength  randint(1, str_dice) * 0.06      organisation  randint(1, org_dice) * 0.053
    with extra dice when the shooters' armor beats the target's piercing, and half damage when
    the target's armor beats the shooters' piercing.

The dice sizes and damage factors are the game's (data/constants.json); the A/(A+D') hit rule and
hit_rate are the simulator's own, standing in for the game's per-battalion hit resolution.
"""

from __future__ import annotations

import random
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from .model import Constants, Division


def stochastic_round(x: float, rng: random.Random) -> int:
    whole = int(x)
    return whole + (1 if rng.random() < x - whole else 0)


def division_attack(div: Division, targets: Sequence[Division]) -> float:
    """Soft/hard attack blended by the targets' hardness, scaled by remaining strength."""
    weights = [max(t.strength, 1e-9) for t in targets]
    hardness = sum(t.template.hardness * w for t, w in zip(targets, weights, strict=True)) / sum(weights)
    tpl = div.template
    return (tpl.soft_attack * (1 - hardness) + tpl.hard_attack * hardness) * div.strength_frac


def attack_total(
    shooters: Sequence[Division], targets: Sequence[Division], mods: Mapping[str, float]
) -> float:
    return sum(division_attack(d, targets) * max(0.0, 1 + mods.get(d.id, 0.0)) for d in shooters)


def defense_total(units: Sequence[Division], mods: Mapping[str, float]) -> float:
    return sum(d.template.defense * d.strength_frac * max(0.0, 1 + mods.get(d.id, 0.0)) for d in units)


def breakthrough_total(units: Sequence[Division]) -> float:
    return sum(d.template.breakthrough * d.strength_frac for d in units)


def hit_chance(attack: float, defense: float, breakthrough: float, c: Constants) -> float:
    if attack <= 0:
        return 0.0
    eff = max(defense - breakthrough, 0.25 * defense)
    return min(1.0, c.combat("hit_pass_scale") * attack / (attack + eff))


def expected_hits(attack: float, defense: float, breakthrough: float, c: Constants) -> float:
    return attack * c.combat("hit_rate") * hit_chance(attack, defense, breakthrough, c)


@dataclass
class Damage:
    strength: dict[str, float] = field(default_factory=dict)
    org: dict[str, float] = field(default_factory=dict)
    hits: int = 0


def _weighted(units: Sequence[Division], attr: str) -> float:
    total = sum(max(u.strength, 1e-9) for u in units)
    return sum(getattr(u.template, attr) * max(u.strength, 1e-9) for u in units) / total


def shoot(
    shooters: Sequence[Division],
    targets: Sequence[Division],
    shooter_mods: Mapping[str, float],
    target_def_mods: Mapping[str, float],
    shooter_is_attacker: bool,
    rng: random.Random,
    c: Constants,
) -> Damage:
    """Damage one side does to the other this hour (not yet applied)."""
    dmg = Damage()
    if not shooters or not targets:
        return dmg
    a = attack_total(shooters, targets, shooter_mods)
    d = defense_total(targets, target_def_mods)
    b = breakthrough_total(shooters) if shooter_is_attacker else 0.0
    p = hit_chance(a, d, b, c)
    attempts = stochastic_round(a * c.combat("hit_rate"), rng)
    armor = _weighted(shooters, "armor")
    piercing = _weighted(shooters, "piercing")
    for _ in range(attempts):
        if rng.random() >= p:
            continue
        target = targets[rng.randrange(len(targets))]
        str_dice = c.combat("str_dice")
        org_dice = c.combat("org_dice")
        if armor > target.template.piercing:
            str_dice += c.combat("str_dice_armor_extra")
            org_dice += c.combat("org_dice_armor_extra")
        deflect_s = deflect_o = 1.0
        if target.template.armor > piercing:
            deflect_s = 1 - c.combat("str_armor_deflection")
            deflect_o = 1 - c.combat("org_armor_deflection")
        s = rng.randint(1, int(str_dice)) * c.combat("str_damage_modifier") * deflect_s
        o = rng.randint(1, int(org_dice)) * c.combat("org_damage_modifier") * deflect_o
        dmg.strength[target.id] = dmg.strength.get(target.id, 0.0) + s
        dmg.org[target.id] = dmg.org.get(target.id, 0.0) + o
        dmg.hits += 1
    return dmg
