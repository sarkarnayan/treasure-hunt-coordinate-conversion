import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import (
    create_riddle_image,
    finish_team_timer,
    find_nearest_place,
    get_or_assign_place,
    get_team_timer,
    haversine_km,
    load_config,
    format_elapsed_time,
    room_for_assigned_coordinates,
    room_group_index,
    start_team_timer,
    validate_coordinates,
    validate_config,
    verify_admin_credentials,
)


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

    def test_riddle_is_rendered_as_an_image(self):
        image = create_riddle_image("A clue with punctuation—“and quotes.”")

        self.assertEqual(image.mode, "RGB")
        self.assertEqual(image.width, 1200)
        self.assertGreater(image.height, 200)

    def test_backend_config_passes_admin_save_validation(self):
        validate_config(load_config())

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

    def test_elapsed_time_uses_hours_minutes_and_seconds(self):
        self.assertEqual(format_elapsed_time(3661), "01:01:01")
        self.assertEqual(format_elapsed_time(-1), "00:00:00")


if __name__ == "__main__":
    unittest.main()
