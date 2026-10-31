"""With an even --seeds, compare's score cell is statistics.median (the mean of the
two middle runs) while the cost cells come from the lower-middle run, so the row's
score and spend do not describe the same run (docs/evaluation.md says they do)."""
from hoi4_harness.config import HarnessConfig
from hoi4_harness.eval.comparison import Comparison, ModelResult
from hoi4_harness.eval.metrics import ScoreCard
from hoi4_harness.eval.scenarios import SCENARIOS
from hoi4_harness.eval.variance import AggregateCard


def test_even_seed_row_is_one_run():
    low = ScoreCard("economy_ramp", score=0.0, planner_calls=10)
    high = ScoreCard("economy_ramp", score=1.0, planner_calls=90)
    agg = AggregateCard("economy_ramp", seeds=[1936, 1937], cards=[low, high])
    result = ModelResult("scripted:scripted", card=low, aggregate=agg)
    comp = Comparison(SCENARIOS["economy_ramp"], HarnessConfig(), [result], seeds=2)
    cells = comp._row(result)
    score_cell, calls_cell = cells[1], cells[4]
    shown_run = {"10": low, "90": high}[calls_cell]
    assert float(score_cell) == shown_run.score, (
        f"row shows score {score_cell} beside the calls of a run that scored {shown_run.score}")
