import unittest

from app import (
    find_nearest_place,
    haversine_km,
    room_group_index,
    validate_coordinates,
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


if __name__ == "__main__":
    unittest.main()
