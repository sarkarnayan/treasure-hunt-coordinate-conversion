import json
import math
from pathlib import Path
from typing import Any, Optional


CONFIG_PATH = Path(__file__).with_name("places.json")
LOGO_PATH = Path(__file__).with_name("assets") / "inxiteout_logo.jpg"


def validate_coordinates(latitude: float, longitude: float) -> None:
    if not -90 <= latitude <= 90:
        raise ValueError("Latitude must be between -90 and 90.")
    if not -180 <= longitude <= 180:
        raise ValueError("Longitude must be between -180 and 180.")


def haversine_km(
    latitude_a: float,
    longitude_a: float,
    latitude_b: float,
    longitude_b: float,
) -> float:
    earth_radius_km = 6371.0088
    lat_a, lat_b = math.radians(latitude_a), math.radians(latitude_b)
    delta_lat = math.radians(latitude_b - latitude_a)
    delta_lon = math.radians(longitude_b - longitude_a)
    value = (
        math.sin(delta_lat / 2) ** 2
        + math.cos(lat_a) * math.cos(lat_b) * math.sin(delta_lon / 2) ** 2
    )
    return 2 * earth_radius_km * math.asin(math.sqrt(value))


def find_nearest_place(
    latitude: float, longitude: float, places: list[dict[str, Any]]
) -> Optional[tuple[int, dict[str, Any], str, float]]:
    validate_coordinates(latitude, longitude)
    nearest: Optional[tuple[int, dict[str, Any], str, float]] = None

    for place_index, place in enumerate(places):
        for location in place["locations"]:
            distance = haversine_km(
                latitude,
                longitude,
                location["latitude"],
                location["longitude"],
            )
            if nearest is None or distance < nearest[3]:
                nearest = (place_index, place, location["name"], distance)

    return nearest


def room_group_index(place_index: int) -> int:
    if place_index < 0:
        raise ValueError("Place index cannot be negative.")
    if place_index < 4:
        return 0
    return 1 + (place_index - 4) // 5


def load_config() -> dict[str, Any]:
    with CONFIG_PATH.open(encoding="utf-8") as config_file:
        config = json.load(config_file)
    if not isinstance(config.get("places"), list) or not config["places"]:
        raise ValueError("Configuration must contain a non-empty places list.")
    radius = config.get("match_radius_km")
    if not isinstance(radius, (int, float)) or radius <= 0:
        raise ValueError("match_radius_km must be a positive number.")
    teams = config.get("teams")
    if not isinstance(teams, list) or not teams or not all(
        isinstance(team, str) and team.strip() for team in teams
    ):
        raise ValueError("Configuration must contain non-empty team names.")
    rounds = config.get("rounds")
    if not isinstance(rounds, list) or not rounds or not all(
        isinstance(round_number, int) and round_number > 0
        for round_number in rounds
    ):
        raise ValueError("Configuration must contain positive round numbers.")
    room_numbers = config.get("room_numbers")
    expected_room_count = room_group_index(len(config["places"]) - 1) + 1
    if not isinstance(room_numbers, list) or len(room_numbers) < expected_room_count:
        raise ValueError("Configuration must include a room number for every place group.")
    return config


def main() -> None:
    import streamlit as st

    st.set_page_config(page_title="Treasure Hunt", page_icon="🧭")
    logo_column, title_column = st.columns([1, 5], vertical_alignment="center")
    with logo_column:
        st.image(str(LOGO_PATH), width=90)
    with title_column:
        st.title("Treasure Hunt")

    try:
        config = load_config()
    except (OSError, json.JSONDecodeError, ValueError) as error:
        st.error(f"Could not load places.json: {error}")
        return

    if "screen" not in st.session_state:
        st.session_state.screen = "team"

    if st.session_state.screen == "team":
        st.subheader("Select your team")
        with st.form("team_selection"):
            selected_team = st.selectbox("Team", config["teams"])
            continue_to_round = st.form_submit_button("Continue", type="primary")
        if continue_to_round:
            st.session_state.selected_team = selected_team
            st.session_state.screen = "round"
            st.rerun()
        return

    st.caption(f"Team: {st.session_state.selected_team}")

    if st.session_state.screen == "round":
        st.subheader("Select a round")
        with st.form("round_selection"):
            selected_round = st.selectbox(
                "Round",
                config["rounds"],
                format_func=lambda round_number: f"Round {round_number}",
            )
            continue_to_lookup = st.form_submit_button("Continue", type="primary")
        if continue_to_lookup:
            st.session_state.selected_round = selected_round
            st.session_state.screen = "lookup"
            st.rerun()
        if st.button("Back to team selection"):
            st.session_state.screen = "team"
            st.rerun()
        return

    st.caption(f"Round {st.session_state.selected_round}")
    navigation_columns = st.columns(2)
    if navigation_columns[0].button("Back to rounds"):
        st.session_state.screen = "round"
        st.rerun()
    if navigation_columns[1].button("Change team"):
        st.session_state.screen = "team"
        st.rerun()

    input_column, output_column = st.columns(2)
    with input_column:
        st.subheader("Enter coordinates")
        with st.form("coordinate_lookup"):
            latitude = st.number_input(
                "Latitude",
                min_value=-90.0,
                max_value=90.0,
                value=0.0,
                format="%.6f",
            )
            longitude = st.number_input(
                "Longitude",
                min_value=-180.0,
                max_value=180.0,
                value=0.0,
                format="%.6f",
            )
            submitted = st.form_submit_button("Find room", type="primary")

    with output_column:
        st.subheader("Room")
        if submitted:
            nearest = find_nearest_place(latitude, longitude, config["places"])
            if nearest is None:
                st.error("There are no landmarks in the configuration.")
            else:
                place_index, place, _, distance = nearest
                if distance > config["match_radius_km"]:
                    st.warning(
                        f"No landmark found within "
                        f"{config['match_radius_km']:.1f} km. "
                        f"The nearest is {place['name']} ({distance:.2f} km away)."
                    )
                else:
                    room_number = config["room_numbers"][
                        room_group_index(place_index)
                    ]
                    st.success(f"Room {room_number}")
        else:
            st.info("Enter the landmark's coordinates to find its room.")


if __name__ == "__main__":
    main()
