from io import BytesIO
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image
from streamlit.testing.v1 import AppTest

from app import (
    create_riddle_image,
    check_hotel_detective_answers,
    check_whitepaper_answer,
    finish_team_timer,
    find_nearest_place,
    get_or_assign_place,
    get_admin_team_timing,
    get_hidden_photograph_state,
    get_murder_mystery_state,
    get_visual_puzzle_progress,
    get_team_penalty_seconds,
    load_hotel_detective_content,
    load_hidden_photograph_content,
    load_hidden_photograph_submissions,
    load_murder_mystery_content,
    load_whitepaper_questions,
    load_visual_puzzle_content,
    get_team_timer,
    get_unlocked_rounds,
    get_round_unlock_code,
    round_unlock_was_entered,
    haversine_km,
    load_config,
    format_elapsed_time,
    ROUND_NUMBERS,
    room_for_assigned_coordinates,
    room_group_index,
    save_hotel_detective_content,
    save_hidden_photograph_content,
    save_murder_mystery_content,
    save_whitepaper_questions,
    save_visual_puzzle_content,
    start_team_timer,
    start_team_round_timer,
    finish_team_round_timer,
    submit_round_unlock_code,
    submit_visual_puzzle_answer,
    submit_visual_puzzle_final_code,
    submit_hidden_photograph,
    submit_murder_mystery_answer,
    validate_coordinates,
    validate_config,
    team_names_for_count,
    verify_admin_credentials,
    review_hidden_photograph_submission,
    reset_team_games,
)
from murder_mysteries import MURDER_MYSTERIES
from sudoku_puzzles import DEFAULT_SUDOKU_PUZZLES


class CoordinateLookupTests(unittest.TestCase):
    def setUp(self):
        self.places = [
            {
                "name": "Taj Mahal",
                "locations": [
                    {"name": "Taj Mahal", "latitude": 27.1751, "longitude": 78.0421}
                ],
            },
            {
                "name": "Ajanta Ellora Caves",
                "locations": [
                    {"name": "Ajanta Caves", "latitude": 20.5519, "longitude": 75.7033},
                    {"name": "Ellora Caves", "latitude": 20.0268, "longitude": 75.1790},
                ],
            },
        ]

    def test_exact_coordinates_find_configured_place(self):
        match = find_nearest_place(27.1751, 78.0421, self.places)

        self.assertIsNotNone(match)
        place_index, place, location_name, distance = match
        self.assertEqual(place["name"], "Taj Mahal")
        self.assertEqual(room_group_index(place_index), 0)
        self.assertEqual(location_name, "Taj Mahal")
        self.assertAlmostEqual(distance, 0)

    def test_alternate_site_coordinates_match_same_place(self):
        match = find_nearest_place(20.0268, 75.1790, self.places)

        self.assertIsNotNone(match)
        place_index, place, location_name, distance = match
        self.assertEqual(place["name"], "Ajanta Ellora Caves")
        self.assertEqual(room_group_index(place_index), 0)
        self.assertEqual(location_name, "Ellora Caves")
        self.assertAlmostEqual(distance, 0)

    def test_out_of_range_coordinates_are_rejected(self):
        with self.assertRaises(ValueError):
            validate_coordinates(91, 78)
        with self.assertRaises(ValueError):
            find_nearest_place(27, 181, self.places)

    def test_haversine_distance_is_zero_for_same_point(self):
        self.assertAlmostEqual(haversine_km(0, 0, 0, 0), 0)

    def test_room_groups_match_requested_place_number_ranges(self):
        place_indices = [0, 3, 4, 8, 9, 13, 14, 18, 19]
        room_numbers = [room_group_index(index) + 1 for index in place_indices]
        self.assertEqual(room_numbers, [1, 1, 2, 2, 3, 3, 4, 4, 5])

    def test_round_assignments_are_unique_and_persistent_per_team(self):
        config = {"places": [{}, {}, {}]}
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "assignments.sqlite3"
            with (
                patch("app.load_config", return_value=config),
                patch("app.ASSIGNMENT_DB_PATH", database_path),
                patch("app.random.choice", side_effect=lambda places: places[0]),
            ):
                first_team_place = get_or_assign_place("Team 1", 1)
                self.assertEqual(get_or_assign_place("Team 1", 1), first_team_place)
                second_team_place = get_or_assign_place("Team 2", 1)
                next_round_place = get_or_assign_place("Team 1", 2)

        self.assertNotEqual(first_team_place, second_team_place)
        self.assertEqual(next_round_place, first_team_place)

    def test_room_is_revealed_only_for_coordinates_matching_team_clue(self):
        room_numbers = ["101"]

        self.assertEqual(
            room_for_assigned_coordinates(
                27.1751, 78.0421, self.places, 0, 2.0, room_numbers
            ),
            "101",
        )
        self.assertIsNone(
            room_for_assigned_coordinates(
                20.0268, 75.1790, self.places, 0, 2.0, room_numbers
            )
        )
        self.assertIsNone(
            room_for_assigned_coordinates(
                27.2, 78.0, self.places, 0, 2.0, room_numbers
            )
        )

    def test_place_room_number_overrides_group_room_number(self):
        places = [
            {
                "name": "Taj Mahal",
                "room_number": "301",
                "locations": [
                    {"name": "Taj Mahal", "latitude": 27.1751, "longitude": 78.0421}
                ],
            }
        ]

        self.assertEqual(
            room_for_assigned_coordinates(
                27.1751, 78.0421, places, 0, 2.0, ["legacy room"]
            ),
            "301",
        )

    def test_riddle_is_rendered_as_an_image(self):
        image = create_riddle_image("A clue with punctuation—“and quotes.”")

        self.assertEqual(image.mode, "RGB")
        self.assertEqual(image.width, 1200)
        self.assertGreater(image.height, 200)

    def test_backend_config_passes_admin_save_validation(self):
        config = load_config()
        validate_config(config)
        self.assertNotIn("rounds", config)
        self.assertEqual(ROUND_NUMBERS, [1, 2, 3, 4, 5, 6])
        self.assertEqual(config["round_penalties"]["4"]["free_attempts"], 3)
        self.assertEqual(config["round_penalties"]["5"]["penalty_seconds"], 120)

    def test_legacy_config_loads_default_round_penalty_settings(self):
        config = load_config()
        config.pop("round_penalties")
        config["rounds"] = [1]
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "places.json"
            config_path.write_text(json.dumps(config), encoding="utf-8")
            with patch("app.CONFIG_PATH", config_path):
                loaded = load_config()

        self.assertEqual(loaded["round_penalties"]["4"]["free_attempts"], 3)
        self.assertEqual(loaded["round_penalties"]["6"]["penalty_seconds"], 120)
        self.assertNotIn("rounds", loaded)

    def test_round_penalty_config_rejects_negative_values(self):
        config = load_config()
        config["round_penalties"]["4"]["free_attempts"] = -1

        with self.assertRaisesRegex(ValueError, "between 0 and 100"):
            validate_config(config)

    def test_backend_config_requires_a_puzzle_and_room_for_each_place(self):
        config = load_config()
        config["places"][0]["puzzle"] = ""

        with self.assertRaisesRegex(ValueError, "puzzle, room number"):
            validate_config(config)

    def test_team_count_preserves_names_and_generates_unique_defaults(self):
        self.assertEqual(
            team_names_for_count(["Alpha", "Team 2"], 4),
            ["Alpha", "Team 2", "Team 1", "Team 3"],
        )
        self.assertEqual(team_names_for_count(["Alpha", "Beta"], 1), ["Alpha"])

    def test_team_count_must_be_positive(self):
        with self.assertRaisesRegex(ValueError, "at least 1"):
            team_names_for_count(["Alpha"], 0)

    def test_admin_login_requires_both_credentials(self):
        self.assertTrue(
            verify_admin_credentials("admin", "secret", "admin", "secret")
        )
        self.assertFalse(
            verify_admin_credentials("admin", "wrong", "admin", "secret")
        )
        self.assertFalse(
            verify_admin_credentials("other", "secret", "admin", "secret")
        )

    def test_team_timer_persists_and_finishes_only_once(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch("app.ASSIGNMENT_DB_PATH", Path(directory) / "timers.sqlite3"):
                with patch("app.time.time", return_value=1000):
                    started = start_team_timer("Team 1")
                with patch("app.time.time", return_value=1050):
                    resumed = start_team_timer("Team 1")
                    finished = finish_team_timer("Team 1")
                with patch("app.time.time", return_value=1100):
                    still_finished = finish_team_timer("Team 1")
                persisted = get_team_timer("Team 1")

        self.assertEqual(started, (1000, None))
        self.assertEqual(resumed, started)
        self.assertEqual(finished, (1000, 1050))
        self.assertEqual(still_finished, finished)
        self.assertEqual(persisted, finished)

    def test_rounds_unlock_in_order(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch("app.ASSIGNMENT_DB_PATH", Path(directory) / "unlock.sqlite3"):
                rounds = [1, 2, 3, 4, 5, 6]
                self.assertEqual(get_unlocked_rounds("Team 1", rounds), [1])
                start_team_round_timer("Team 1", 1)
                finish_team_round_timer("Team 1", 1)
                self.assertEqual(get_unlocked_rounds("Team 1", rounds), [1, 2])
                for number in rounds[1:]:
                    start_team_round_timer("Team 1", number)
                    finish_team_round_timer("Team 1", number)
                self.assertEqual(get_unlocked_rounds("Team 1", rounds), rounds)

    def test_round_entry_requires_previous_round_code(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch("app.ASSIGNMENT_DB_PATH", Path(directory) / "round-codes.sqlite3"):
                start_team_round_timer("Team 1", 1)
                finish_team_round_timer("Team 1", 1)
                code = get_round_unlock_code("Team 1", 2)

                self.assertIsNotNone(code)
                if code is None:
                    self.fail("Round 1 should issue a Round 2 entry code.")
                self.assertEqual(get_round_unlock_code("Team 1", 2), code)
                self.assertFalse(round_unlock_was_entered("Team 1", 2))
                self.assertFalse(submit_round_unlock_code("Team 1", 2, "wrong"))
                self.assertFalse(round_unlock_was_entered("Team 1", 2))
                self.assertTrue(submit_round_unlock_code("Team 1", 2, code))
                self.assertTrue(round_unlock_was_entered("Team 1", 2))

    def test_completed_round_code_is_used_for_next_round_and_reset(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch("app.ASSIGNMENT_DB_PATH", Path(directory) / "round-codes.sqlite3"):
                start_team_round_timer("Team 1", 2)
                finish_team_round_timer("Team 1", 2, unlock_code="CODE-3")

                self.assertEqual(get_round_unlock_code("Team 1", 3), "CODE-3")
                reset_team_games(["Team 1"], from_round=2)
                start_team_round_timer("Team 1", 2)
                finish_team_round_timer("Team 1", 2)
                self.assertNotEqual(get_round_unlock_code("Team 1", 3), "CODE-3")

    def test_team_round_timer_persists_and_finishes_only_once(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch("app.ASSIGNMENT_DB_PATH", Path(directory) / "round-timers.sqlite3"):
                with patch("app.time.time", return_value=1000):
                    started = start_team_round_timer("Team 1", 1)
                with patch("app.time.time", return_value=1050):
                    resumed = start_team_round_timer("Team 1", 1)
                    finished = finish_team_round_timer("Team 1", 1)
                with patch("app.time.time", return_value=1100):
                    still_finished = finish_team_round_timer("Team 1", 1)

        self.assertEqual(started, (1000, None))
        self.assertEqual(resumed, started)
        self.assertEqual(finished, (1000, 1050))
        self.assertEqual(still_finished, finished)

    def test_admin_team_timing_includes_rounds_penalties_and_unstarted_teams(self):
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "admin-timing.sqlite3"
            with patch("app.ASSIGNMENT_DB_PATH", database_path):
                with patch("app.time.time", return_value=1000):
                    start_team_timer("Team 1")
                with patch("app.time.time", return_value=1010):
                    start_team_round_timer("Team 1", 1)
                with patch("app.time.time", return_value=1040):
                    finish_team_round_timer("Team 1", 1)
                with patch("app.time.time", return_value=1050):
                    start_team_round_timer("Team 1", 2)

                get_team_penalty_seconds("Team 1")
                connection = sqlite3.connect(database_path)
                try:
                    connection.execute(
                        """
                        INSERT INTO visual_puzzle_progress (team, penalty_seconds)
                        VALUES (?, ?)
                        """,
                        ("Team 1", 120),
                    )
                    connection.commit()
                finally:
                    connection.close()

                with patch("app.time.time", return_value=1100):
                    summaries = get_admin_team_timing(
                        ["Team 1", "Team 2"], [1, 2]
                    )

        first_team = summaries[0]
        self.assertEqual(first_team["elapsed_seconds"], 100)
        self.assertEqual(first_team["penalty_seconds"], 120)
        self.assertEqual(first_team["adjusted_seconds"], 220)
        self.assertEqual(
            first_team["rounds"],
            [
                {"round_number": 1, "elapsed_seconds": 30, "completed": True},
                {"round_number": 2, "elapsed_seconds": 50, "completed": False},
            ],
        )
        self.assertIsNone(summaries[1]["elapsed_seconds"])
        self.assertIsNone(summaries[1]["rounds"][0]["elapsed_seconds"])

    def test_visual_puzzle_content_and_sequential_progress_persist(self):
        image_buffer = BytesIO()
        Image.new("RGB", (4, 4), "red").save(image_buffer, format="PNG")
        image_data = image_buffer.getvalue()
        puzzles = [
            {
                "Image": image_data,
                "Correct answer": f"Answer {position}",
                "Code segment": f"{position:016d}",
            }
            for position in range(1, 7)
        ]
        with tempfile.TemporaryDirectory() as directory:
            sample_puzzles = patch(
                "app.random.sample",
                side_effect=[[3, 1, 4, 2], [6, 5, 4, 1]],
            )
            sample_mock = sample_puzzles.start()
            self.addCleanup(sample_puzzles.stop)
            with (
                patch("app.ASSIGNMENT_DB_PATH", Path(directory) / "visual.sqlite3"),
            ):
                save_visual_puzzle_content(puzzles)
                loaded = load_visual_puzzle_content()
                progress = get_visual_puzzle_progress("Team 1")
                self.assertEqual(progress["puzzle_positions"], [3, 1, 4, 2])
                other_team_progress = get_visual_puzzle_progress("Team 2")
                self.assertEqual(
                    other_team_progress["puzzle_positions"], [6, 5, 4, 1]
                )

                for _ in range(3):
                    third_free_attempt = submit_visual_puzzle_answer(
                        "Team 1", "wrong"
                    )
                fourth_attempt = submit_visual_puzzle_answer("Team 1", "wrong")
                after_wrong_answers = get_visual_puzzle_progress("Team 1")
                solved = submit_visual_puzzle_answer(
                    "Team 1", "  ANSWER   3  "
                )
                after_solve = get_visual_puzzle_progress("Team 1")
                persisted_penalty = get_team_penalty_seconds("Team 1")
                next_image_wrong = submit_visual_puzzle_answer(
                    "Team 1", "wrong again"
                )
                self.assertEqual(sample_mock.call_count, 2)

        self.assertEqual(len(loaded), 6)
        self.assertEqual(
            loaded[0],
            {"Position": 1, **puzzles[0]},
        )
        self.assertEqual(third_free_attempt["penalty_seconds"], 0)
        self.assertEqual(fourth_attempt["penalty_seconds"], 120)
        self.assertEqual(after_wrong_answers["wrong_attempts"], 4)
        self.assertEqual(after_wrong_answers["current_position"], 1)
        self.assertEqual(solved["segment"], "0000000000000003")
        self.assertEqual(after_solve["current_position"], 2)
        self.assertEqual(after_solve["wrong_attempts"], 0)
        self.assertEqual(after_solve["penalty_seconds"], 120)
        self.assertEqual(persisted_penalty, 120)
        self.assertEqual(next_image_wrong["penalty_seconds"], 120)

    def test_visual_puzzle_uses_configured_free_attempts_and_penalty(self):
        image_buffer = BytesIO()
        Image.new("RGB", (2, 2), "red").save(image_buffer, format="PNG")
        puzzles = [
            {
                "Image": image_buffer.getvalue(),
                "Correct answer": f"Answer {position}",
                "Code segment": f"{position:016d}",
            }
            for position in range(1, 5)
        ]
        with tempfile.TemporaryDirectory() as directory:
            with (
                patch("app.ASSIGNMENT_DB_PATH", Path(directory) / "visual.sqlite3"),
                patch("app.random.sample", return_value=[1, 2, 3, 4]),
                patch(
                    "app.load_config",
                    return_value={
                        "round_penalties": {
                            "4": {"free_attempts": 1, "penalty_seconds": 45}
                        }
                    },
                ),
            ):
                save_visual_puzzle_content(puzzles)
                free_attempt = submit_visual_puzzle_answer("Team 1", "wrong")
                penalized_attempt = submit_visual_puzzle_answer("Team 1", "wrong")

        self.assertEqual(free_attempt["penalty_seconds"], 0)
        self.assertEqual(penalized_attempt["penalty_seconds"], 45)

    def test_visual_puzzle_requires_all_solutions_before_final_code(self):
        image_buffer = BytesIO()
        Image.new("RGB", (2, 2), "blue").save(image_buffer, format="PNG")
        puzzles = [
            {
                "Image": image_buffer.getvalue(),
                "Correct answer": f"Answer {position}",
                "Code segment": f"{position:016d}",
            }
            for position in range(1, 5)
        ]
        selected_positions = [3, 1, 4, 2]
        expected_code = "".join(
            puzzles[position - 1]["Code segment"]
            for position in selected_positions
        )
        with tempfile.TemporaryDirectory() as directory:
            with (
                patch("app.ASSIGNMENT_DB_PATH", Path(directory) / "visual.sqlite3"),
                patch("app.random.sample", return_value=selected_positions),
            ):
                save_visual_puzzle_content(puzzles)
                with self.assertRaisesRegex(ValueError, "Solve all four"):
                    submit_visual_puzzle_final_code("Team 1", expected_code)
                for position in selected_positions:
                    result = submit_visual_puzzle_answer(
                        "Team 1", f"Answer {position}"
                    )
                    self.assertTrue(result["is_correct"])
                self.assertFalse(
                    submit_visual_puzzle_final_code(
                        "Team 1", "0" * 64
                    )
                )
                self.assertTrue(
                    submit_visual_puzzle_final_code("Team 1", expected_code)
                )
                completed = get_visual_puzzle_progress("Team 1")

        self.assertTrue(completed["completed"])

    def test_visual_puzzle_rejects_partial_content_and_non_16_digit_segments(self):
        image_buffer = BytesIO()
        Image.new("RGB", (2, 2), "green").save(image_buffer, format="PNG")
        image_data = image_buffer.getvalue()
        one_puzzle = {
            "Image": image_data,
            "Correct answer": "Answer",
            "Code segment": "0000000000000001",
        }
        invalid_puzzle = dict(one_puzzle)
        invalid_puzzle["Code segment"] = "123"
        with tempfile.TemporaryDirectory() as directory:
            with patch("app.ASSIGNMENT_DB_PATH", Path(directory) / "visual.sqlite3"):
                with self.assertRaisesRegex(ValueError, "between 4 and 50"):
                    save_visual_puzzle_content([one_puzzle] * 3)
                with self.assertRaisesRegex(ValueError, "exactly 16 digits"):
                    save_visual_puzzle_content([invalid_puzzle] * 4)

    def test_hidden_photographs_assign_uniquely_reveal_code_and_review_latest_photo(self):
        reference_buffer = BytesIO()
        Image.new("RGB", (4, 4), "red").save(reference_buffer, format="PNG")
        reference_image = reference_buffer.getvalue()
        replacement_buffer = BytesIO()
        Image.new("RGB", (4, 4), "blue").save(replacement_buffer, format="PNG")
        replacement_image = replacement_buffer.getvalue()
        photographs = [
            {"Image": reference_image, "Code": f"CODE-{position}"}
            for position in range(1, 16)
        ]

        with tempfile.TemporaryDirectory() as directory:
            with (
                patch(
                    "app.ASSIGNMENT_DB_PATH",
                    Path(directory) / "hidden_photographs.sqlite3",
                ),
                patch("app.random.choice", side_effect=lambda positions: positions[0]),
            ):
                save_hidden_photograph_content(photographs)
                first_team = get_hidden_photograph_state("Team 1")
                second_team = get_hidden_photograph_state("Team 2")
                self.assertEqual(
                    submit_hidden_photograph("Team 1", reference_image),
                    "CODE-1",
                )
                self.assertEqual(
                    submit_hidden_photograph("Team 1", replacement_image),
                    "CODE-1",
                )
                submitted = get_hidden_photograph_state("Team 1")
                submissions = load_hidden_photograph_submissions()
                penalty = review_hidden_photograph_submission(
                    "Team 1", is_correct=False
                )
                reviewed = get_hidden_photograph_state("Team 1")
                persisted_penalty = get_team_penalty_seconds("Team 1")
                with self.assertRaisesRegex(ValueError, "already been reviewed"):
                    submit_hidden_photograph("Team 1", reference_image)
                with self.assertRaisesRegex(ValueError, "already been reviewed"):
                    review_hidden_photograph_submission(
                        "Team 1", is_correct=False
                    )

        self.assertNotEqual(
            first_team["photograph_position"],
            second_team["photograph_position"],
        )
        self.assertIsNone(first_team["code"])
        self.assertEqual(submitted["code"], "CODE-1")
        self.assertEqual(submitted["submission_image"], replacement_image)
        self.assertFalse(submitted["reviewed"])
        self.assertEqual(len(submissions), 1)
        self.assertEqual(submissions[0]["submission_image"], replacement_image)
        self.assertEqual(penalty, 120)
        self.assertTrue(reviewed["reviewed"])
        self.assertFalse(reviewed["is_correct"])
        self.assertEqual(persisted_penalty, 120)

    def test_hidden_photograph_uses_configured_review_penalty(self):
        image_buffer = BytesIO()
        Image.new("RGB", (2, 2), "green").save(image_buffer, format="PNG")
        image_data = image_buffer.getvalue()
        photographs = [
            {"Image": image_data, "Code": f"CODE-{position}"}
            for position in range(1, 16)
        ]
        with tempfile.TemporaryDirectory() as directory:
            with (
                patch(
                    "app.ASSIGNMENT_DB_PATH",
                    Path(directory) / "hidden_photographs.sqlite3",
                ),
                patch("app.random.choice", side_effect=lambda positions: positions[0]),
                patch(
                    "app.load_config",
                    return_value={
                        "round_penalties": {"5": {"penalty_seconds": 75}}
                    },
                ),
            ):
                save_hidden_photograph_content(photographs)
                submit_hidden_photograph("Team 1", image_data)
                penalty = review_hidden_photograph_submission(
                    "Team 1", is_correct=False
                )

        self.assertEqual(penalty, 75)

    def test_hidden_photographs_require_at_least_fifteen_complete_entries(self):
        image_buffer = BytesIO()
        Image.new("RGB", (2, 2), "green").save(image_buffer, format="PNG")
        photograph = {"Image": image_buffer.getvalue(), "Code": "CODE"}
        with tempfile.TemporaryDirectory() as directory:
            with patch(
                "app.ASSIGNMENT_DB_PATH",
                Path(directory) / "hidden_photographs.sqlite3",
            ):
                with self.assertRaisesRegex(ValueError, "at least 15"):
                    save_hidden_photograph_content([photograph] * 14)
                incomplete_photographs = [dict(photograph) for _ in range(15)]
                incomplete_photographs[4]["Code"] = ""
                with self.assertRaisesRegex(ValueError, "needs an image and a code"):
                    save_hidden_photograph_content(incomplete_photographs)

    def test_round_six_has_fifteen_cases_with_at_least_eight_characters_each(self):
        self.assertEqual(len(MURDER_MYSTERIES), 15)
        self.assertEqual(len(DEFAULT_SUDOKU_PUZZLES), 15)
        self.assertEqual(
            len({puzzle["puzzle"] for puzzle in DEFAULT_SUDOKU_PUZZLES}), 15
        )
        for case in MURDER_MYSTERIES:
            with self.subTest(case=case["title"]):
                self.assertGreaterEqual(len(case["characters"]), 8)
                character_names = [character["name"] for character in case["characters"]]
                self.assertEqual(len(set(character_names)), len(character_names))
                self.assertIn(case["culprit"], character_names)
                self.assertTrue(case["culprit"])
        for puzzle in DEFAULT_SUDOKU_PUZZLES:
            with self.subTest(puzzle=puzzle["puzzle"]):
                self.assertEqual(puzzle["puzzle"].count("0"), 10)

    def test_murder_mystery_and_sudoku_progress_and_penalties_are_independent(self):
        with tempfile.TemporaryDirectory() as directory:
            with (
                patch(
                    "app.ASSIGNMENT_DB_PATH",
                    Path(directory) / "murder_mysteries.sqlite3",
                ),
                patch("app.random.choice", side_effect=lambda positions: positions[0]),
            ):
                initial = get_murder_mystery_state("Team 1")
                second_team = get_murder_mystery_state("Team 2")
                self.assertEqual(
                    get_murder_mystery_state("Team 1")["case_position"],
                    initial["case_position"],
                )
                self.assertNotEqual(
                    initial["case_position"], second_team["case_position"]
                )
                self.assertNotIn("culprit", initial["case"])
                self.assertNotIn("sudoku_solution", initial["case"])
                sudoku_solution = load_murder_mystery_content()[
                    initial["case_position"] - 1
                ]["sudoku_solution"].replace("\n", "")

                for _ in range(3):
                    free_miss = submit_murder_mystery_answer(
                        "Team 1", "mystery", "wrong suspect"
                    )
                penalized_miss = submit_murder_mystery_answer(
                    "Team 1", "mystery", "wrong suspect"
                )
                with self.assertRaisesRegex(ValueError, "Fill every 4x4 Sudoku"):
                    submit_murder_mystery_answer(
                        "Team 1", "sudoku", "0" * 16
                    )
                free_sudoku_miss = None
                for _ in range(3):
                    free_sudoku_miss = submit_murder_mystery_answer(
                        "Team 1", "sudoku", "1" * 16
                    )
                penalized_sudoku_miss = submit_murder_mystery_answer(
                    "Team 1", "sudoku", "1" * 16
                )
                correct_sudoku = submit_murder_mystery_answer(
                    "Team 1",
                    "sudoku",
                    sudoku_solution,
                )
                correct_mystery = submit_murder_mystery_answer(
                    "Team 1",
                    "mystery",
                    MURDER_MYSTERIES[initial["case_position"] - 1]["culprit"],
                )
                completed = get_murder_mystery_state("Team 1")
                penalty = get_team_penalty_seconds("Team 1")
                admin_timing = get_admin_team_timing(["Team 1"], [6])

        self.assertEqual(free_miss["attempts"], 3)
        self.assertEqual(free_miss["added_penalty"], 0)
        self.assertEqual(penalized_miss["attempts"], 4)
        self.assertEqual(penalized_miss["added_penalty"], 120)
        self.assertEqual(free_sudoku_miss["attempts"], 3)
        self.assertEqual(free_sudoku_miss["added_penalty"], 0)
        self.assertEqual(penalized_sudoku_miss["attempts"], 4)
        self.assertEqual(penalized_sudoku_miss["added_penalty"], 120)
        self.assertTrue(correct_sudoku["is_correct"])
        self.assertTrue(correct_mystery["is_correct"])
        self.assertTrue(completed["mystery_solved"])
        self.assertTrue(completed["sudoku_solved"])
        self.assertEqual(penalty, 240)
        self.assertEqual(admin_timing[0]["penalty_seconds"], 240)

    def test_round_six_uses_configured_attempts_for_both_puzzles(self):
        with tempfile.TemporaryDirectory() as directory:
            with (
                patch(
                    "app.ASSIGNMENT_DB_PATH",
                    Path(directory) / "murder_mysteries.sqlite3",
                ),
                patch("app.random.choice", side_effect=lambda positions: positions[0]),
                patch(
                    "app.load_config",
                    return_value={
                        "round_penalties": {
                            "6": {"free_attempts": 1, "penalty_seconds": 75}
                        }
                    },
                ),
            ):
                mystery_free = submit_murder_mystery_answer(
                    "Team 1", "mystery", "wrong suspect"
                )
                mystery_penalized = submit_murder_mystery_answer(
                    "Team 1", "mystery", "wrong suspect"
                )
                sudoku_free = submit_murder_mystery_answer(
                    "Team 1", "sudoku", "1" * 16
                )
                sudoku_penalized = submit_murder_mystery_answer(
                    "Team 1", "sudoku", "1" * 16
                )

        self.assertEqual(mystery_free["added_penalty"], 0)
        self.assertEqual(mystery_penalized["added_penalty"], 75)
        self.assertEqual(sudoku_free["added_penalty"], 0)
        self.assertEqual(sudoku_penalized["added_penalty"], 75)

    def test_round_six_assigns_all_fifteen_cases_uniquely(self):
        with tempfile.TemporaryDirectory() as directory:
            with (
                patch(
                    "app.ASSIGNMENT_DB_PATH",
                    Path(directory) / "murder_assignments.sqlite3",
                ),
                patch("app.random.choice", side_effect=lambda positions: positions[0]),
            ):
                assigned_positions = [
                    get_murder_mystery_state(f"Team {index}")["case_position"]
                    for index in range(1, 16)
                ]
                with self.assertRaisesRegex(ValueError, "No unassigned"):
                    get_murder_mystery_state("Team 16")

        self.assertEqual(len(set(assigned_positions)), 15)

    def test_round_six_admin_content_persists_and_resets_assignments_and_progress(self):
        with tempfile.TemporaryDirectory() as directory:
            with (
                patch(
                    "app.ASSIGNMENT_DB_PATH",
                    Path(directory) / "murder_admin.sqlite3",
                ),
                patch("app.random.choice", side_effect=lambda positions: positions[0]),
            ):
                initial = get_murder_mystery_state("Team 1")
                submit_murder_mystery_answer("Team 1", "mystery", "wrong")
                cases = load_murder_mystery_content()
                cases[0]["title"] = "Updated Case Title"
                cases[0]["briefing"] = "Updated murder mystery."
                cases[0]["characters"][0]["role"] = "updated role"
                cases[0]["evidence"][0] = "Updated clue."
                cases[0]["culprit"] = cases[0]["characters"][1]["name"]
                cases[0]["sudoku_puzzle"] = "\n".join(
                    DEFAULT_SUDOKU_PUZZLES[1]["puzzle"][index : index + 4]
                    for index in range(0, 16, 4)
                )
                cases[0]["sudoku_solution"] = "\n".join(
                    DEFAULT_SUDOKU_PUZZLES[1]["solution"][index : index + 4]
                    for index in range(0, 16, 4)
                )
                save_murder_mystery_content(cases)
                loaded = load_murder_mystery_content()
                refreshed = get_murder_mystery_state("Team 1")

                invalid_cases = load_murder_mystery_content()
                invalid_cases[0]["characters"] = invalid_cases[0]["characters"][:7]
                with self.assertRaisesRegex(ValueError, "at least eight"):
                    save_murder_mystery_content(invalid_cases)

        self.assertEqual(initial["case_position"], 1)
        self.assertEqual(loaded[0]["title"], "Updated Case Title")
        self.assertEqual(loaded[0]["briefing"], "Updated murder mystery.")
        self.assertEqual(loaded[0]["characters"][0]["role"], "updated role")
        self.assertEqual(loaded[0]["evidence"][0], "Updated clue.")
        self.assertEqual(
            loaded[0]["sudoku_puzzle"],
            "\n".join(
                DEFAULT_SUDOKU_PUZZLES[1]["puzzle"][index : index + 4]
                for index in range(0, 16, 4)
            ),
        )
        self.assertEqual(refreshed["case_position"], 1)
        self.assertEqual(refreshed["case"]["title"], "Updated Case Title")
        self.assertNotIn("sudoku_solution", refreshed["case"])
        self.assertEqual(refreshed["mystery_attempts"], 0)
        self.assertFalse(refreshed["mystery_solved"])

    def test_round_six_migrates_existing_nine_by_nine_sudoku_storage(self):
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "legacy_round_six.sqlite3"
            connection = sqlite3.connect(database_path)
            try:
                connection.execute(
                    """
                    CREATE TABLE murder_mystery_content (
                        position INTEGER PRIMARY KEY,
                        title TEXT NOT NULL,
                        briefing TEXT NOT NULL,
                        characters_json TEXT NOT NULL,
                        evidence_json TEXT NOT NULL,
                        culprit TEXT NOT NULL,
                        word_puzzle TEXT NOT NULL,
                        word_answer TEXT NOT NULL,
                        sudoku_puzzle TEXT NOT NULL,
                        sudoku_solution TEXT NOT NULL
                    )
                    """
                )
                connection.execute(
                    """
                    CREATE TABLE murder_mystery_assignments (
                        team TEXT PRIMARY KEY,
                        case_position INTEGER NOT NULL UNIQUE
                    )
                    """
                )
                connection.execute(
                    """
                    CREATE TABLE murder_mystery_progress (
                        team TEXT PRIMARY KEY,
                        mystery_attempts INTEGER NOT NULL DEFAULT 0,
                        word_attempts INTEGER NOT NULL DEFAULT 0,
                        mystery_solved INTEGER NOT NULL DEFAULT 0,
                        word_solved INTEGER NOT NULL DEFAULT 0,
                        sudoku_attempts INTEGER NOT NULL DEFAULT 0,
                        sudoku_solved INTEGER NOT NULL DEFAULT 0,
                        penalty_seconds INTEGER NOT NULL DEFAULT 0
                    )
                    """
                )
                connection.executemany(
                    """
                    INSERT INTO murder_mystery_content (
                        position, title, briefing, characters_json, evidence_json,
                        culprit, word_puzzle, word_answer, sudoku_puzzle,
                        sudoku_solution
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            position,
                            case["title"],
                            case["briefing"],
                            json.dumps(case["characters"]),
                            json.dumps(case["evidence"]),
                            case["culprit"],
                            "Old word puzzle",
                            "old answer",
                            "0" * 81,
                            "1" * 81,
                        )
                        for position, case in enumerate(MURDER_MYSTERIES, start=1)
                    ],
                )
                connection.execute(
                    "INSERT INTO murder_mystery_assignments VALUES ('Team 1', 1)"
                )
                connection.execute(
                    """
                    INSERT INTO murder_mystery_progress (
                        team, mystery_attempts, word_attempts, mystery_solved,
                        word_solved, sudoku_attempts, sudoku_solved,
                        penalty_seconds
                    ) VALUES ('Team 1', 4, 2, 0, 0, 3, 0, 120)
                    """
                )
                connection.commit()
            finally:
                connection.close()

            with (
                patch("app.ASSIGNMENT_DB_PATH", database_path),
                patch("app.random.choice", side_effect=lambda positions: positions[-1]),
            ):
                migrated = get_murder_mystery_state("Team 1")
                migrated_content = load_murder_mystery_content()

                connection = sqlite3.connect(database_path)
                try:
                    connection.execute(
                        """
                        UPDATE murder_mystery_content
                        SET sudoku_puzzle = ?, sudoku_solution = ?
                        """,
                        ("0" * 81, "1" * 81),
                    )
                    connection.execute(
                        "UPDATE murder_mystery_assignments "
                        "SET case_position = 1 WHERE team = 'Team 1'"
                    )
                    connection.execute(
                        """
                        UPDATE murder_mystery_progress
                        SET mystery_attempts = 4, sudoku_attempts = 3,
                            penalty_seconds = 120
                        WHERE team = 'Team 1'
                        """
                    )
                    connection.commit()
                finally:
                    connection.close()

                recovered = get_murder_mystery_state("Team 1")

        self.assertEqual(migrated["case_position"], 15)
        self.assertEqual(migrated["mystery_attempts"], 0)
        self.assertEqual(migrated["sudoku_attempts"], 0)
        self.assertEqual(migrated["penalty_seconds"], 0)
        self.assertEqual(len(migrated["case"]["sudoku_puzzle"]), 16)
        self.assertEqual(
            migrated["case"]["sudoku_puzzle"],
            DEFAULT_SUDOKU_PUZZLES[14]["puzzle"],
        )
        self.assertEqual(len(migrated_content), 15)
        self.assertEqual(recovered["case_position"], 15)
        self.assertEqual(recovered["mystery_attempts"], 0)
        self.assertEqual(recovered["sudoku_attempts"], 0)
        self.assertEqual(recovered["penalty_seconds"], 0)
        self.assertEqual(len(recovered["case"]["sudoku_puzzle"]), 16)

    def test_hotel_detective_saves_statements_and_reveals_code_only_when_correct(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch("app.ASSIGNMENT_DB_PATH", Path(directory) / "hotel.sqlite3"):
                statements = [
                    {
                        "Statement": f"Hotel fact {index}",
                        "Correct answer": "True" if index % 2 else "False",
                    }
                    for index in range(1, 21)
                ]
                codes = ["CODE-1", "CODE-2", "CODE-3", "CODE-4"]
                save_hotel_detective_content(statements, codes)

                loaded_statements, loaded_codes = load_hotel_detective_content()
                correct_answers = [
                    index % 2 == 1 for index in range(1, 6)
                ]
                correct_result = check_hotel_detective_answers(1, correct_answers)
                incorrect_result = check_hotel_detective_answers(
                    1, [False] * 5
                )

        self.assertEqual(len(loaded_statements), 20)
        self.assertEqual(loaded_statements[0]["Statement"], "Hotel fact 1")
        self.assertEqual(loaded_codes, codes)
        self.assertEqual(correct_result, (True, "CODE-1"))
        self.assertEqual(incorrect_result, (False, None))

    def test_round_two_shows_hotel_statements_after_round_one(self):
        with tempfile.TemporaryDirectory() as directory:
            with (
                patch("app.ASSIGNMENT_DB_PATH", Path(directory) / "game.sqlite3"),
                patch("app.render_team_timer"),
            ):
                save_hotel_detective_content(
                    [
                        {"Statement": f"Hotel fact {index}", "Correct answer": "True"}
                        for index in range(1, 21)
                    ],
                    ["CODE-1", "CODE-2", "CODE-3", "CODE-4"],
                )
                start_team_timer("Team 1")
                start_team_round_timer("Team 1", 1)
                finish_team_round_timer("Team 1", 1)
                app = AppTest.from_string("from app import main\nmain()", default_timeout=15)
                app.session_state.screen = "round"
                app.session_state.selected_team = "Team 1"
                app.run()
                self.assertNotIn(
                    "Back to team selection",
                    [button.label for button in app.button],
                )
                self.assertNotIn(
                    "Change team",
                    [button.label for button in app.button],
                )
                app.button[0].click().run()
                self.assertEqual(len(app.number_input), 2)

                app.button[0].click().run()
                app.selectbox[0].set_value(2)
                app.button[0].click().run()

                self.assertFalse(app.exception)
                self.assertTrue(
                    any(button.label == "Unlock round" for button in app.button)
                )
                self.assertFalse(
                    any("Hotel Detective" in heading.value for heading in app.subheader)
                )
                connection = sqlite3.connect(Path(directory) / "game.sqlite3")
                try:
                    round_two_timer = connection.execute(
                        """
                        SELECT 1 FROM team_round_timers
                        WHERE team = ? AND round_number = ?
                        """,
                        ("Team 1", 2),
                    ).fetchone()
                finally:
                    connection.close()
                self.assertIsNone(round_two_timer)
                round_two_code = get_round_unlock_code("Team 1", 2)
                self.assertIsNotNone(round_two_code)
                if round_two_code is None:
                    self.fail("Round 1 should issue a Round 2 entry code.")
                app.text_input[0].set_value(round_two_code)
                next(
                    button
                    for button in app.button
                    if button.label == "Unlock round"
                ).click().run()

                self.assertFalse(app.exception)
                connection = sqlite3.connect(Path(directory) / "game.sqlite3")
                try:
                    round_two_timer = connection.execute(
                        """
                        SELECT 1 FROM team_round_timers
                        WHERE team = ? AND round_number = ?
                        """,
                        ("Team 1", 2),
                    ).fetchone()
                finally:
                    connection.close()
                self.assertIsNotNone(round_two_timer)
                self.assertTrue(
                    any("Hotel Detective" in heading.value for heading in app.subheader)
                )
                self.assertTrue(any("Hotel fact 1" in text.value for text in app.markdown))
                self.assertEqual(len(app.number_input), 0)
                self.assertEqual(app.selectbox[0].label, "Statement set")

    def test_hotel_detective_rejects_answerless_statement_and_unconfigured_set(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch("app.ASSIGNMENT_DB_PATH", Path(directory) / "hotel.sqlite3"):
                blank_rows = [
                    {"Statement": "", "Correct answer": ""}
                    for _ in range(20)
                ]
                blank_rows[0] = {
                    "Statement": "A hotel fact",
                    "Correct answer": "",
                }
                with self.assertRaises(ValueError):
                    save_hotel_detective_content(blank_rows, [""] * 4)

                save_hotel_detective_content(
                    [
                        {"Statement": None, "Correct answer": None}
                        for _ in range(20)
                    ],
                    [None] * 4,
                )
                with self.assertRaisesRegex(ValueError, "not ready"):
                    check_hotel_detective_answers(1, [True] * 5)

    def test_whitepaper_questions_persist_and_reveal_code_only_for_correct_answer(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch("app.ASSIGNMENT_DB_PATH", Path(directory) / "whitepaper.sqlite3"):
                questions = [
                    {
                        "Question": f"Whitepaper question {index}",
                        "Correct answer": (
                            "Café answer" if index == 1 else f"Correct answer {index}"
                        ),
                        "Unlock code": f"CODE-{index}",
                    }
                    for index in range(1, 21)
                ]
                save_whitepaper_questions(questions)

                loaded_questions = load_whitepaper_questions()
                correct_result = check_whitepaper_answer(1, " CAFÉ   answer ")
                incorrect_result = check_whitepaper_answer(1, "wrong answer")

        self.assertEqual(len(loaded_questions), 20)
        self.assertEqual(loaded_questions[0], questions[0])
        self.assertEqual(correct_result, (True, "CODE-1"))
        self.assertEqual(incorrect_result, (False, None))

    def test_whitepaper_questions_reject_incomplete_entries_and_unconfigured_questions(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch("app.ASSIGNMENT_DB_PATH", Path(directory) / "whitepaper.sqlite3"):
                with self.assertRaisesRegex(ValueError, "needs a question"):
                    save_whitepaper_questions(
                        [
                            {
                                "Question": "A question",
                                "Correct answer": "An answer",
                                "Unlock code": "",
                            }
                        ]
                        + [
                            {
                                "Question": "",
                                "Correct answer": "",
                                "Unlock code": "",
                            }
                        ]
                        * 19
                    )

                save_whitepaper_questions(
                    [
                        {
                            "Question": "",
                            "Correct answer": "",
                            "Unlock code": "",
                        }
                    ]
                    * 20
                )
                with self.assertRaisesRegex(ValueError, "not ready"):
                    check_whitepaper_answer(1, "anything")

    def test_elapsed_time_uses_hours_minutes_and_seconds(self):
        self.assertEqual(format_elapsed_time(3661), "01:01:01")
        self.assertEqual(format_elapsed_time(-1), "00:00:00")


if __name__ == "__main__":
    unittest.main()
