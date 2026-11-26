"""Evaluation on held-out episodes: the learned policy against the rule baseline.

What can and cannot be measured from logged decisions. The data are the LLM's (or a controller's)
own choices, so a policy that picks a different order has no logged outcome. Three numbers, each
with its limit stated in the report:

* reward-model error on the held-out rows against predicting the training mean (does the model
  learn anything that generalises at all);
* matched-action reward: the mean logged outcome of the held-out rows where the policy would have
  chosen the order that was actually given, with how many rows that is (unbiased only if the logging
  policy chose without regard to anything the outcome depends on, which is false here);
* the model's own estimate of each policy's value (direct method): biased towards the learned
  policy, which is the model's own argmax.

The verdict is "inconclusive" unless the matched sets are large enough; with one run it will say so.
"""

from __future__ import annotations

from .baseline import baseline_from_features
from .learn import LinearBandit
from .ope import compare, estimate, usable

#: Matched rows needed per policy before a difference is read as anything.
MIN_MATCHED = 30
#: ... and the margin (in controlled states per horizon) it has to clear.
MIN_MARGIN = 0.25
#: Effective sample size (ope.py) each policy needs before the off-policy comparison is read.
MIN_ESS = 30.0


def _mean(xs: list[float]) -> float | None:
    return sum(xs) / len(xs) if xs else None


def evaluate(model: LinearBandit, train: list[dict], test: list[dict]) -> dict:
    train = [r for r in train if r.get("reward") is not None]
    test = [r for r in test if r.get("reward") is not None]
    out: dict = {"train_rows": len(train), "test_rows": len(test), "trusted_orders": model.trusted(),
                 "counts": model.counts}
    if not test:
        out["verdict"] = "inconclusive: no held-out rows with an outcome"
        return out
    mean_train = _mean([r["reward"] for r in train]) or 0.0
    policies = {"baseline": lambda r: baseline_from_features(r["features"]),
                "learned": lambda r: model.act(r["features"]),
                "logged": lambda r: r["action"]}
    err_model, err_const, n_pred = 0.0, 0.0, 0
    for r in test:
        pred = model.predict(r["features"]).get(r["action"])
        if pred is None:
            continue
        err_model += (pred - r["reward"]) ** 2
        err_const += (mean_train - r["reward"]) ** 2
        n_pred += 1
    out["reward_model"] = {"rows_scored": n_pred,
                           "mse_model": err_model / n_pred if n_pred else None,
                           "mse_train_mean": err_const / n_pred if n_pred else None}
    out["logged_mean_reward"] = _mean([r["reward"] for r in test])
    for name, policy in policies.items():
        matched = [r["reward"] for r in test if policy(r) == r["action"]]
        entry = {"matched": len(matched), "matched_mean_reward": _mean(matched),
                 "coverage": len(matched) / len(test)}
        preds = [model.predict(r["features"]).get(policy(r)) for r in test]
        preds = [p for p in preds if p is not None]
        entry["model_estimate"] = _mean(preds)
        entry["model_estimate_rows"] = len(preds)
        out[name] = entry
        out[f"{name}_orders"] = {o: sum(1 for r in test if policy(r) == o)
                                 for o in sorted({policy(r) for r in test})}
    b, g = out["baseline"], out["learned"]
    if usable(test):
        _ope_section(out, model, train, test, mean_train, baseline=policies["baseline"],
                     learned=policies["learned"])
        return out
    if min(b["matched"], g["matched"]) < MIN_MATCHED:
        out["verdict"] = (f"inconclusive: {b['matched']} baseline-matched and {g['matched']} "
                          f"learned-matched held-out rows, need {MIN_MATCHED} each")
    else:
        diff = g["matched_mean_reward"] - b["matched_mean_reward"]
        words = ("learned ahead of baseline" if diff >= MIN_MARGIN else
                 "baseline ahead of learned" if diff <= -MIN_MARGIN else "no difference beyond the margin")
        out["verdict"] = f"{words} ({diff:+.2f} states per horizon on matched rows)"
    return out


def _ope_section(out: dict, model: LinearBandit, train: list[dict], test: list[dict], mean_train: float,
                 baseline, learned) -> None:
    """Held-out rows that carry propensities (exploration data): score both policies by inverse
    propensity and doubly robust estimates, and read the paired difference with its interval."""
    pols = {"baseline": lambda r: baseline({"features": r}), "learned": lambda r: learned({"features": r})}
    est = {name: estimate(test, pol, model, mean_train) for name, pol in pols.items()}
    cmp = compare(test, pols["learned"], pols["baseline"], model, mean_train)
    out["ope"] = {"rows": est["baseline"].get("n", 0), "baseline": est["baseline"], "learned": est["learned"],
                  "learned_minus_baseline": cmp}
    ess = min(est["baseline"].get("ess", 0.0), est["learned"].get("ess", 0.0))
    ci = cmp.get("ci")
    if ess < MIN_ESS or ci is None:
        out["verdict"] = (f"inconclusive: effective sample {ess:.0f} for the weaker policy, "
                          f"need {MIN_ESS:.0f} (off-policy estimate from {cmp['n']} logged rows)")
        return
    d = cmp["difference"]
    words = ("learned ahead of baseline" if ci[0] > 0 else
             "baseline ahead of learned" if ci[1] < 0 else "no difference the data can separate")
    out["verdict"] = (f"{words} (off-policy, doubly robust {d:+.2f} per decision, "
                      f"95% interval {ci[0]:+.2f} to {ci[1]:+.2f})")


def render(report: dict) -> str:
    lines = ["# Micro controller evaluation", "",
             f"Train rows {report['train_rows']}, held-out rows {report['test_rows']}; "
             f"orders the model trusts: {', '.join(report['trusted_orders']) or 'none'}.", ""]
    if "reward_model" in report:
        rm = report["reward_model"]
        mse_m = "n/a" if rm["mse_model"] is None else f"{rm['mse_model']:.3f}"
        mse_c = "n/a" if rm["mse_train_mean"] is None else f"{rm['mse_train_mean']:.3f}"
        lines += ["Reward model on held-out rows: MSE "
                  f"{mse_m} against {mse_c} for the training mean ({rm['rows_scored']} rows scored).", ""]
        lines += ["| policy | matched rows | matched mean reward | model estimate |",
                  "|---|---|---|---|"]
        for name in ("baseline", "learned", "logged"):
            e = report[name]
            mm = "n/a" if e["matched_mean_reward"] is None else f"{e['matched_mean_reward']:+.2f}"
            me = "n/a" if e["model_estimate"] is None else f"{e['model_estimate']:+.2f}"
            lines.append(f"| {name} | {e['matched']} ({e['coverage']:.0%}) | {mm} | {me} |")
        lines.append("")
    if "ope" in report:
        o = report["ope"]
        lines += ["Off-policy estimates from logged exploration (ope.py), value per decision:", "",
                  "| policy | ipw | self-normalised | doubly robust (95% interval) | eff. n | off support |",
                  "|---|---|---|---|---|---|"]
        for name in ("baseline", "learned"):
            e = o[name]
            ci = e.get("dr_ci")
            span = f" ({ci[0]:+.2f} to {ci[1]:+.2f})" if ci else ""
            sn = "n/a" if e.get("snips") is None else f"{e['snips']:+.2f}"
            lines.append(f"| {name} | {e['ipw']:+.2f} | {sn} | {e['dr']:+.2f}{span} | "
                         f"{e['ess']:.0f} | {e['off_support']:.0%} |")
        lines.append("")
    lines += [f"Verdict: {report['verdict']}.", "",
              "Reward is the change in controlled states over the horizon, less a charge for divisions lost, "
              "for the whole theatre, not for the one order. Matched rows are the held-out decisions where "
              "the policy agrees with what was logged; the model estimate is the model's own opinion and "
              "favours the learned policy. Neither is a win rate against players."]
    return "\n".join(lines)
