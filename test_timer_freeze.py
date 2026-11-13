import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from app import (
    finish_team_round_timer,
    finish_team_timer,
    format_elapsed_time,
    get_active_timer_freezes,
    get_admin_team_timing,
    get_team_reset_revision,
    get_team_timer,
    get_unlocked_rounds,
    reset_team_games,
    set_timer_freeze,
    start_team_round_timer,
    start_team_timer,
)


class TimerFreezeTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        database = patch("app.ASSIGNMENT_DB_PATH", Path(self.directory.name) / "game.sqlite3")
        database.start()
        self.addCleanup(database.stop)

    def at(self, timestamp, function, *args):
        with patch("app.time.time", return_value=timestamp):
            return function(*args)

    def summary(self, timestamp, team="Team 1"):
        return self.at(timestamp, get_admin_team_timing, [team], [1, 2])[0]

    def test_round_freeze_pauses_total_only_during_that_round(self):
        self.at(90, set_timer_freeze, True, "Team 1", 1)
        self.at(100, start_team_timer, "Team 1")
        self.at(110, start_team_round_timer, "Team 1", 1)
        self.at(130, set_timer_freeze, False, "Team 1", 1)
        self.at(150, finish_team_round_timer, "Team 1", 1)
        self.at(160, start_team_round_timer, "Team 1", 2)
        self.at(170, set_timer_freeze, True, "Team 1", 1)

        result = self.summary(180)
        self.assertEqual(result["elapsed_seconds"], 60)
        self.assertEqual(result["rounds"][0]["elapsed_seconds"], 20)
        self.assertEqual(result["rounds"][1]["elapsed_seconds"], 20)

    def test_overlapping_master_and_individual_freezes_are_not_double_counted(self):
        self.at(100, start_team_timer, "Team 1")
        self.at(100, start_team_round_timer, "Team 1", 1)
        self.at(100, start_team_timer, "Team 2")
        self.at(100, start_team_round_timer, "Team 2", 1)
        self.at(110, set_timer_freeze, True, "Team 1", 1)
        self.at(120, set_timer_freeze, True)
        self.at(140, set_timer_freeze, False)

        self.assertEqual(get_active_timer_freezes(), (False, {("Team 1", 1)}))
        self.assertEqual(self.summary(150)["elapsed_seconds"], 10)
        self.assertEqual(self.summary(150)["rounds"][0]["elapsed_seconds"], 10)
        self.assertEqual(self.summary(150, "Team 2")["elapsed_seconds"], 30)
        self.at(160, set_timer_freeze, False, "Team 1", 1)
        self.assertEqual(self.summary(170)["elapsed_seconds"], 20)

    def test_start_and_finish_during_master_freeze_and_repeated_actions(self):
        self.at(100, set_timer_freeze, True)
        self.at(105, set_timer_freeze, True)
        self.at(110, start_team_timer, "Team 1")
        self.at(120, start_team_round_timer, "Team 1", 1)
        self.at(130, finish_team_round_timer, "Team 1", 1)
        self.at(140, finish_team_timer, "Team 1")
        self.at(150, set_timer_freeze, False)
        self.at(160, set_timer_freeze, False)
        result = self.summary(170)
        self.assertEqual(result["elapsed_seconds"], 0)
        self.assertEqual(result["rounds"][0]["elapsed_seconds"], 0)
        self.assertTrue(result["finished"])

    def test_completion_during_individual_freeze_resumes_team_total(self):
        self.at(100, start_team_timer, "Team 1")
        self.at(100, start_team_round_timer, "Team 1", 1)
        self.at(110, set_timer_freeze, True, "Team 1", 1)
        self.at(130, finish_team_round_timer, "Team 1", 1)
        result = self.summary(150)
        self.assertEqual(result["elapsed_seconds"], 30)
        self.assertEqual(result["rounds"][0]["elapsed_seconds"], 10)
        self.assertTrue(result["rounds"][0]["completed"])

    def test_player_timer_shows_freeze_and_keeps_penalties(self):
        start_team_timer("Team 1")
        start_team_round_timer("Team 1", 1)
        set_timer_freeze(True, "Team 1", 1)
        with closing(sqlite3.connect(Path(self.directory.name) / "game.sqlite3")) as connection, connection:
            connection.execute(
                "INSERT INTO visual_puzzle_progress (team, penalty_seconds) VALUES (?, ?)",
                ("Team 1", 120),
            )
        app = AppTest.from_string(
            "import streamlit as st\nfrom app import render_team_timer\n"
            "render_team_timer(st, 'Team 1')",
            default_timeout=15,
        ).run()
        self.assertFalse(app.exception)
        summary = get_admin_team_timing(["Team 1"], [1])[0]
        self.assertEqual(summary["penalty_seconds"], 120)
        self.assertEqual(app.metric[0].value, format_elapsed_time(summary["adjusted_seconds"]))
        self.assertTrue(any("Timer frozen" in caption.value for caption in app.caption))

    def test_dashboard_controls_individual_and_master_freezes(self):
        with patch.dict(os.environ, {
            "TREASURE_HUNT_ADMIN_USER": "admin",
            "TREASURE_HUNT_ADMIN_PASSWORD": "test-password",
        }):
            app = AppTest.from_string("from app import main\nmain()", default_timeout=15)
            app.session_state.admin_authenticated = True
            app.run()
            app.sidebar.radio[0].set_value("Admin").run()
            self.assertFalse(app.exception)
            self.assertEqual(app.selectbox[0].label, "Freeze team")
            team = app.selectbox[0].value
            app.selectbox[1].set_value(2).run()
            next(button for button in app.button if button.label == "Freeze selected round").click().run()
            self.assertFalse(app.exception)
            self.assertEqual(get_active_timer_freezes(), (False, {(team, 2)}))
            next(button for button in app.button if button.label == "Freeze all timers").click().run()
            self.assertEqual(get_active_timer_freezes(), (True, {(team, 2)}))
            next(button for button in app.button if button.label == "Resume all timers").click().run()
            self.assertEqual(get_active_timer_freezes(), (False, {(team, 2)}))
            next(button for button in app.button if button.label == "Resume selected round").click().run()
            self.assertEqual(get_active_timer_freezes(), (False, set()))
            self.assertFalse(app.exception)

    def seed_completed_team(self, team, position):
        self.at(100, start_team_timer, team)
        for number in range(1, 7):
            self.at(100 + number, start_team_round_timer, team, number)
            self.at(110 + number, finish_team_round_timer, team, number)
        self.at(120, finish_team_timer, team)
        with closing(sqlite3.connect(Path(self.directory.name) / "game.sqlite3")) as connection, connection:
            connection.execute(
                "INSERT INTO assignments (team, round_number, place_index) VALUES (?, 1, ?)",
                (team, position),
            )
            connection.execute(
                "INSERT INTO visual_puzzle_progress VALUES (?, 5, 2, 120, 1)",
                (team,),
            )
            connection.execute(
                "INSERT INTO visual_puzzle_assignments VALUES (?, 1, ?)", (team, position)
            )
            connection.execute(
                "INSERT INTO hidden_photograph_assignments VALUES (?, ?)", (team, position)
            )
            connection.execute(
                """INSERT INTO hidden_photograph_submissions
                   (team, image, submitted_at, penalty_seconds) VALUES (?, ?, 110, 30)""",
                (team, b"photo"),
            )
            connection.execute(
                "INSERT INTO murder_mystery_assignments VALUES (?, ?)", (team, position)
            )
            connection.execute(
                """INSERT INTO murder_mystery_progress
                   (team, mystery_solved, sudoku_solved, penalty_seconds)
                   VALUES (?, 1, 1, 60)""",
                (team,),
            )

    def test_reset_from_round_clears_later_progress_and_preserves_earlier_state(self):
        self.seed_completed_team("Team 1", 1)
        self.seed_completed_team("Team 2", 2)
        self.at(121, set_timer_freeze, True)
        self.at(122, set_timer_freeze, True, "Team 1", 5)
        reset_team_games(["Team 1"], 5)

        self.assertIsNone(get_team_timer("Team 1"))
        self.assertEqual(get_unlocked_rounds("Team 1", list(range(1, 7))), [1, 2, 3, 4, 5])
        self.assertEqual(get_team_reset_revision("Team 1"), 1)
        self.assertEqual(get_team_reset_revision("Team 2"), 0)
        self.assertEqual(get_team_timer("Team 2"), (100, 120))
        self.assertEqual(get_active_timer_freezes(), (True, {("Team 1", 5)}))
        with closing(sqlite3.connect(Path(self.directory.name) / "game.sqlite3")) as connection:
            self.assertEqual(connection.execute(
                "SELECT penalty_seconds, completed FROM visual_puzzle_progress WHERE team = ?",
                ("Team 1",),
            ).fetchone(), (120, 1))
            for table in ("hidden_photograph_submissions", "murder_mystery_progress"):
                self.assertIsNone(connection.execute(
                    f"SELECT team FROM {table} WHERE team = ?", ("Team 1",)
                ).fetchone())
            for table in ("assignments", "visual_puzzle_assignments",
                          "hidden_photograph_assignments", "murder_mystery_assignments"):
                self.assertIsNotNone(connection.execute(
                    f"SELECT team FROM {table} WHERE team = ?", ("Team 1",)
                ).fetchone())
        self.at(125, set_timer_freeze, False)
        self.at(130, start_team_timer, "Team 1")
        self.at(131, start_team_round_timer, "Team 1", 5)
        result = self.summary(150)
        self.assertEqual(result["elapsed_seconds"], 1)
        self.assertEqual(result["penalty_seconds"], 120)

    def test_all_teams_reset_clears_progress_but_retains_assignments_and_freezes(self):
        for index in (1, 2):
            self.seed_completed_team(f"Team {index}", index)
        self.at(121, set_timer_freeze, True, "Team 2", 4)
        reset_team_games(["Team 1", "Team 2"])
        for team in ("Team 1", "Team 2"):
            self.assertIsNone(get_team_timer(team))
            self.assertEqual(get_unlocked_rounds(team, list(range(1, 7))), [1])
            self.assertEqual(get_team_reset_revision(team), 1)
        self.assertEqual(get_active_timer_freezes(), (False, {("Team 2", 4)}))
        with closing(sqlite3.connect(Path(self.directory.name) / "game.sqlite3")) as connection:
            self.assertEqual(connection.execute(
                "SELECT current_position, wrong_attempts, penalty_seconds, completed "
                "FROM visual_puzzle_progress WHERE team = ?", ("Team 1",)
            ).fetchone(), (1, 0, 0, 0))
            for table in ("assignments", "visual_puzzle_assignments",
                          "hidden_photograph_assignments", "murder_mystery_assignments"):
                self.assertEqual(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0], 2)

    def test_player_session_returns_to_instructions_after_reset(self):
        start_team_timer("Team 1")
        with patch("app.render_team_timer"):
            app = AppTest.from_string("from app import main\nmain()", default_timeout=15)
            app.session_state.screen = "round"
            app.session_state.selected_team = "Team 1"
            app.session_state.team_reset_revision = 0
            app.run()
            self.assertFalse(app.exception)
            reset_team_games(["Team 1"])
            app.run()
            self.assertFalse(app.exception)
            self.assertEqual(app.session_state.screen, "instructions")
            self.assertFalse(app.session_state.rules_acknowledged)
            self.assertTrue(any("admin reset" in message.value for message in app.info))
            self.assertIsNone(get_team_timer("Team 1"))

    def test_reset_rolls_back_if_progress_cannot_be_cleared(self):
        self.seed_completed_team("Team 1", 1)
        with closing(sqlite3.connect(Path(self.directory.name) / "game.sqlite3")) as connection, connection:
            connection.execute(
                """CREATE TRIGGER block_reset BEFORE DELETE ON murder_mystery_progress
                   BEGIN SELECT RAISE(ABORT, 'reset blocked'); END"""
            )
        with self.assertRaisesRegex(sqlite3.IntegrityError, "reset blocked"):
            reset_team_games(["Team 1"], 5)
        self.assertEqual(get_team_timer("Team 1"), (100, 120))
        self.assertEqual(get_unlocked_rounds("Team 1", list(range(1, 7))), list(range(1, 7)))
        self.assertEqual(get_team_reset_revision("Team 1"), 0)
        with closing(sqlite3.connect(Path(self.directory.name) / "game.sqlite3")) as connection:
            self.assertIsNotNone(connection.execute(
                "SELECT team FROM hidden_photograph_submissions WHERE team = 'Team 1'"
            ).fetchone())

    def test_dashboard_reset_requires_confirmation(self):
        start_team_timer("Team 1")
        with patch.dict(os.environ, {
            "TREASURE_HUNT_ADMIN_USER": "admin",
            "TREASURE_HUNT_ADMIN_PASSWORD": "test-password",
        }):
            app = AppTest.from_string("from app import main\nmain()", default_timeout=15)
            app.session_state.admin_authenticated = True
            app.run()
            app.sidebar.radio[0].set_value("Admin").run()
            next(select for select in app.selectbox if select.label == "Reset team").set_value("Team 1")
            next(button for button in app.button if button.label == "Reset game").click().run()
            self.assertTrue(any("Confirm" in error.value for error in app.error))
            self.assertIsNotNone(get_team_timer("Team 1"))
            app.checkbox[0].check()
            next(button for button in app.button if button.label == "Reset game").click().run()
            self.assertFalse(app.exception)
            self.assertIsNone(get_team_timer("Team 1"))
            self.assertEqual(get_team_reset_revision("Team 1"), 1)

    def test_dashboard_round_and_all_team_reset_scopes(self):
        self.seed_completed_team("Team 1", 1)
        self.seed_completed_team("Team 2", 2)
        with patch.dict(os.environ, {
            "TREASURE_HUNT_ADMIN_USER": "admin",
            "TREASURE_HUNT_ADMIN_PASSWORD": "test-password",
        }):
            app = AppTest.from_string("from app import main\nmain()", default_timeout=15)
            app.session_state.admin_authenticated = True
            app.run()
            app.sidebar.radio[0].set_value("Admin").run()
            next(select for select in app.selectbox if select.label == "Reset team").set_value("Team 1")
            next(select for select in app.selectbox if select.label == "Reset from round").set_value(5)
            app.checkbox[0].check()
            next(button for button in app.button if button.label == "Reset game").click().run()
            self.assertFalse(app.exception)
            self.assertEqual(get_unlocked_rounds("Team 1", list(range(1, 7))), [1, 2, 3, 4, 5])
            self.assertIsNotNone(get_team_timer("Team 2"))
            next(select for select in app.selectbox if select.label == "Reset scope").set_value("All teams")
            app.checkbox[0].check()
            next(button for button in app.button if button.label == "Reset game").click().run()
            self.assertFalse(app.exception)
            self.assertIsNone(get_team_timer("Team 2"))
            self.assertEqual(get_unlocked_rounds("Team 1", list(range(1, 7))), [1])


if __name__ == "__main__":
    unittest.main()
