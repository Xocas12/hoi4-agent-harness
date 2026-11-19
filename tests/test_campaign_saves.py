"""Pruning the recovery saves: only what the harness made, only the old ones."""

from __future__ import annotations

from campaign_helpers import LazyClient, build_env, campaign_config
from hoi4_harness.campaign.runner import run_campaign
from hoi4_harness.campaign.saves import SaveTracker, load_harness_saves


def make(save_dir, name, size=10):
    path = save_dir / name
    path.write_bytes(b"x" * size)
    return path


def take(tracker, save_dir, name, date):
    """One recovery save, the way the game makes it: ask, then the file appears."""
    tracker.before_send(date)
    make(save_dir, name)
    tracker.collect()


def dirs(tmp_path):
    saves, run = tmp_path / "saves", tmp_path / "run"
    saves.mkdir()
    run.mkdir()
    return saves, run


def test_only_the_harnesss_own_saves_are_ever_deleted(tmp_path):
    saves, run = dirs(tmp_path)
    users = [make(saves, f"user_{i}.hoi4") for i in range(3)]
    lookalike = make(saves, "GER_1936_01_01_12.hoi4")        # a user save in the game's own pattern
    tracker = SaveTracker(run, saves, keep=2)
    for n in range(6):
        take(tracker, saves, f"GER_1937_{n + 1:02d}_01_14.hoi4", f"1937-{n + 1:02d}-01")
    removed = tracker.prune()
    assert all(p.exists() for p in users) and lookalike.exists()
    assert removed == ["GER_1937_02_01_14.hoi4", "GER_1937_03_01_14.hoi4",
                       "GER_1937_04_01_14.hoi4"]


def test_the_first_and_the_newest_n_are_kept(tmp_path):
    saves, run = dirs(tmp_path)
    tracker = SaveTracker(run, saves, keep=2)
    for n in range(6):
        take(tracker, saves, f"GER_1937_{n + 1:02d}_01_14.hoi4", f"1937-{n + 1:02d}-01")
    tracker.prune()
    assert sorted(p.name for p in saves.glob("*.hoi4")) == [
        "GER_1937_01_01_14.hoi4", "GER_1937_05_01_14.hoi4", "GER_1937_06_01_14.hoi4"]


def test_a_save_the_person_touched_since_is_left_alone(tmp_path):
    saves, run = dirs(tmp_path)
    tracker = SaveTracker(run, saves, keep=1)
    for n in range(4):
        take(tracker, saves, f"GER_1937_{n + 1:02d}_01_14.hoi4", f"1937-{n + 1:02d}-01")
    tracker.data["pending"] = None                      # the claim window is over
    (saves / "GER_1937_02_01_14.hoi4").write_bytes(b"the person saved over it")
    removed = tracker.prune()
    assert "GER_1937_02_01_14.hoi4" not in removed
    assert (saves / "GER_1937_02_01_14.hoi4").read_bytes() == b"the person saved over it"
    assert not (saves / "GER_1937_03_01_14.hoi4").exists()


def test_a_save_made_by_hand_after_the_claim_window_is_never_claimed(tmp_path):
    saves, run = dirs(tmp_path)
    now = [1000.0]
    tracker = SaveTracker(run, saves, keep=1, clock=lambda: now[0])
    take(tracker, saves, "GER_1937_01_01_14.hoi4", "1937-01-01")
    now[0] += 3600
    tracker.collect()                                    # the window closes
    make(saves, "my_own_save.hoi4")
    tracker.collect()
    assert [s["name"] for s in tracker.data["saves"]] == ["GER_1937_01_01_14.hoi4"]


def test_the_record_survives_a_restart_and_a_missing_folder_is_harmless(tmp_path):
    saves, run = dirs(tmp_path)
    tracker = SaveTracker(run, saves, keep=2)
    take(tracker, saves, "GER_1937_01_01_14.hoi4", "1937-01-01")
    again = SaveTracker(run, saves, keep=2)
    assert again.latest()["name"] == "GER_1937_01_01_14.hoi4"
    assert load_harness_saves(run)["saves"]
    assert SaveTracker(run, tmp_path / "nowhere", keep=2).prune() == []


def test_a_fake_campaign_prunes_its_own_saves_and_leaves_the_users(tmp_path):
    config = campaign_config(tmp_path, campaign_until="1937-04-01", campaign_save_months=1,
                             campaign_keep_saves=2)
    env = build_env(config)
    fake = env.adapter.fake_game
    fake.save_dir.mkdir(parents=True, exist_ok=True)
    user = fake.save_dir / "my_game.hoi4"
    user.write_text("mine")
    run_campaign(config, planner=LazyClient(nap=20), env=env)
    names = {s["name"] for s in load_harness_saves(tmp_path / "run")["saves"]}
    on_disk = {p.name for p in fake.save_dir.glob("*.hoi4")} - {"my_game.hoi4"}
    assert user.read_text() == "mine"
    assert len(fake.saves_written) > 4                   # more were taken than are kept
    assert len(on_disk) <= 3 and on_disk <= names
    log = (tmp_path / "run" / "campaign.log").read_text(encoding="utf-8")
    assert "pruned" in log


def test_an_autosave_or_named_save_in_the_window_is_never_claimed(tmp_path):
    saves, run = dirs(tmp_path)
    tracker = SaveTracker(run, saves, keep=1)
    tracker.before_send("1937-01-01")
    make(saves, "autosave.hoi4")                 # the game's own autosave, same window
    make(saves, "my germany.hoi4")               # the person saves under a name of their own
    make(saves, "GER_1937_01_01_14.hoi4")        # what console savegame writes
    tracker.collect()
    assert [s["name"] for s in load_harness_saves(run)["saves"]] == ["GER_1937_01_01_14.hoi4"]
    for n in range(2, 6):
        take(tracker, saves, f"GER_1937_{n:02d}_01_14.hoi4", f"1937-{n:02d}-01")
    tracker.prune()
    assert (saves / "autosave.hoi4").exists() and (saves / "my germany.hoi4").exists()
