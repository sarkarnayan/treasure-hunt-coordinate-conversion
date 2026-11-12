import json
import hmac
import math
import os
import random
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from typing import Any, Optional

from PIL import Image, ImageDraw, ImageFont

from riddles import RIDDLES


CONFIG_PATH = Path(__file__).with_name("places.json")
ASSIGNMENT_DB_PATH = Path(__file__).with_name("assignments.sqlite3")
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


def room_for_assigned_coordinates(
    latitude: float,
    longitude: float,
    places: list[dict[str, Any]],
    assigned_place_index: int,
    match_radius_km: float,
    room_numbers: list[str],
) -> Optional[str]:
    nearest = find_nearest_place(latitude, longitude, places)
    if nearest is None:
        return None

    place_index, _, _, distance = nearest
    if place_index != assigned_place_index or distance > match_radius_km:
        return None
    return room_numbers[room_group_index(place_index)]


def get_or_assign_place(team: str, round_number: int) -> int:
    config = load_config()
    with closing(sqlite3.connect(ASSIGNMENT_DB_PATH, timeout=30)) as connection, connection:
        connection.execute("BEGIN IMMEDIATE")
        create_database_tables(connection)
        assignment = connection.execute(
            "SELECT place_index FROM assignments WHERE round_number = ? AND team = ?",
            (round_number, team),
        ).fetchone()
        if assignment is not None:
            return assignment[0]

        used_places = {
            row[0]
            for row in connection.execute(
                "SELECT place_index FROM assignments WHERE round_number = ?",
                (round_number,),
            )
        }
        available_places = [
            index for index in range(len(config["places"])) if index not in used_places
        ]
        if not available_places:
            raise ValueError("No unassigned places remain for this round.")

        place_index = random.choice(available_places)
        connection.execute(
            "INSERT INTO assignments (round_number, team, place_index) VALUES (?, ?, ?)",
            (round_number, team, place_index),
        )
        return place_index


def create_database_tables(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS assignments (
            round_number INTEGER NOT NULL,
            team TEXT NOT NULL,
            place_index INTEGER NOT NULL,
            PRIMARY KEY (round_number, team),
            UNIQUE (round_number, place_index)
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS team_timers (
            team TEXT PRIMARY KEY,
            started_at REAL NOT NULL,
            finished_at REAL
        )
        """
    )


def get_team_timer(team: str) -> Optional[tuple[float, Optional[float]]]:
    with closing(sqlite3.connect(ASSIGNMENT_DB_PATH, timeout=30)) as connection, connection:
        create_database_tables(connection)
        timer = connection.execute(
            "SELECT started_at, finished_at FROM team_timers WHERE team = ?",
            (team,),
        ).fetchone()
    return timer


def start_team_timer(team: str) -> tuple[float, Optional[float]]:
    with closing(sqlite3.connect(ASSIGNMENT_DB_PATH, timeout=30)) as connection, connection:
        connection.execute("BEGIN IMMEDIATE")
        create_database_tables(connection)
        started_at = time.time()
        connection.execute(
            "INSERT OR IGNORE INTO team_timers (team, started_at) VALUES (?, ?)",
            (team, started_at),
        )
        timer = connection.execute(
            "SELECT started_at, finished_at FROM team_timers WHERE team = ?",
            (team,),
        ).fetchone()
    return timer


def finish_team_timer(team: str) -> tuple[float, Optional[float]]:
    with closing(sqlite3.connect(ASSIGNMENT_DB_PATH, timeout=30)) as connection, connection:
        connection.execute("BEGIN IMMEDIATE")
        create_database_tables(connection)
        connection.execute(
            """
            UPDATE team_timers
            SET finished_at = ?
            WHERE team = ? AND finished_at IS NULL
            """,
            (time.time(), team),
        )
        timer = connection.execute(
            "SELECT started_at, finished_at FROM team_timers WHERE team = ?",
            (team,),
        ).fetchone()
    if timer is None:
        raise ValueError("The team's timer has not been started.")
    return timer


def format_elapsed_time(seconds: float) -> str:
    total_seconds = max(0, int(seconds))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02}:{minutes:02}:{seconds:02}"


def render_team_timer(st: Any, team: str) -> None:
    @st.fragment(run_every="1s")
    def timer_display() -> None:
        timer = get_team_timer(team)
        if timer is None:
            return
        started_at, finished_at = timer
        elapsed = (finished_at or time.time()) - started_at
        label = "Final time" if finished_at is not None else "Time elapsed"
        st.metric(label, format_elapsed_time(elapsed))

    timer_display()


def create_riddle_image(riddle: str) -> Image.Image:
    width = 1200
    padding = 88
    background = (250, 248, 242)
    foreground = (13, 19, 61)
    accent = (111, 53, 240)
    title_font = ImageFont.load_default(size=44)
    body_font = ImageFont.load_default(size=30)

    measuring_draw = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    wrapped_lines: list[str] = []
    for paragraph in riddle.splitlines():
        words = paragraph.split()
        current_line = ""
        for word in words:
            candidate = f"{current_line} {word}".strip()
            if measuring_draw.textlength(candidate, font=body_font) > width - 2 * padding:
                wrapped_lines.append(current_line)
                current_line = word
            else:
                current_line = candidate
        if current_line:
            wrapped_lines.append(current_line)
        wrapped_lines.append("")

    line_height = 48
    height = padding * 2 + 95 + len(wrapped_lines) * line_height
    image = Image.new("RGB", (width, height), background)
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle(
        (24, 24, width - 24, height - 24),
        radius=28,
        outline=accent,
        width=4,
    )
    draw.text((padding, padding), "YOUR CLUE", font=title_font, fill=accent)
    y = padding + 100
    for line in wrapped_lines:
        draw.text((padding, y), line, font=body_font, fill=foreground)
        y += line_height
    return image


def validate_config(config: dict[str, Any]) -> None:
    if not isinstance(config, dict):
        raise ValueError("Configuration must be a JSON object.")
    if not isinstance(config.get("places"), list) or not config["places"]:
        raise ValueError("Configuration must contain a non-empty places list.")
    if len(config["places"]) != len(RIDDLES):
        raise ValueError("There must be exactly one riddle for every configured place.")
    for place in config["places"]:
        if (
            not isinstance(place, dict)
            or not isinstance(place.get("name"), str)
            or not place["name"].strip()
            or not isinstance(place.get("locations"), list)
            or not place["locations"]
        ):
            raise ValueError("Each place needs a name and at least one location.")
        for location in place["locations"]:
            if not isinstance(location, dict) or not isinstance(
                location.get("name"), str
            ) or not location["name"].strip():
                raise ValueError("Each location needs a name and coordinates.")
            latitude = location.get("latitude")
            longitude = location.get("longitude")
            if (
                isinstance(latitude, bool)
                or not isinstance(latitude, (int, float))
                or not math.isfinite(latitude)
                or isinstance(longitude, bool)
                or not isinstance(longitude, (int, float))
                or not math.isfinite(longitude)
            ):
                raise ValueError("Location coordinates must be finite numbers.")
            validate_coordinates(latitude, longitude)

    radius = config.get("match_radius_km")
    if (
        isinstance(radius, bool)
        or not isinstance(radius, (int, float))
        or not math.isfinite(radius)
        or radius <= 0
    ):
        raise ValueError("match_radius_km must be a positive number.")
    teams = config.get("teams")
    if not isinstance(teams, list) or not teams or not all(
        isinstance(team, str) and team.strip() for team in teams
    ):
        raise ValueError("Configuration must contain non-empty team names.")
    if len(teams) > len(config["places"]) or len(set(teams)) != len(teams):
        raise ValueError("Teams must be unique and cannot outnumber configured places.")
    rounds = config.get("rounds")
    if not isinstance(rounds, list) or not rounds or not all(
        isinstance(round_number, int)
        and not isinstance(round_number, bool)
        and round_number > 0
        for round_number in rounds
    ):
        raise ValueError("Configuration must contain positive round numbers.")
    if len(set(rounds)) != len(rounds):
        raise ValueError("Round numbers must be unique.")
    room_numbers = config.get("room_numbers")
    expected_room_count = room_group_index(len(config["places"]) - 1) + 1
    if (
        not isinstance(room_numbers, list)
        or len(room_numbers) < expected_room_count
        or any(
            not (
                isinstance(room, str)
                and room.strip()
                or isinstance(room, (int, float))
                and not isinstance(room, bool)
            )
            for room in room_numbers
        )
    ):
        raise ValueError("Configuration must include a room number for every place group.")
    revision = config.get("config_revision", 0)
    if not isinstance(revision, int) or isinstance(revision, bool) or revision < 0:
        raise ValueError("config_revision must be a non-negative integer.")


def load_config() -> dict[str, Any]:
    with CONFIG_PATH.open(encoding="utf-8") as config_file:
        config = json.load(config_file)
    validate_config(config)
    return config


def save_config(config: dict[str, Any]) -> None:
    validate_config(config)
    temporary_path = CONFIG_PATH.with_suffix(".json.tmp")
    try:
        with temporary_path.open("w", encoding="utf-8") as config_file:
            json.dump(config, config_file, indent=2, ensure_ascii=False)
            config_file.write("\n")
        temporary_path.replace(CONFIG_PATH)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def clear_assignments() -> None:
    with closing(sqlite3.connect(ASSIGNMENT_DB_PATH, timeout=30)) as connection, connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS assignments (
                round_number INTEGER NOT NULL,
                team TEXT NOT NULL,
                place_index INTEGER NOT NULL,
                PRIMARY KEY (round_number, team),
                UNIQUE (round_number, place_index)
            )
            """
        )
        connection.execute("DELETE FROM assignments")


def verify_admin_credentials(
    username: str, password: str, configured_username: str, configured_password: str
) -> bool:
    username_matches = hmac.compare_digest(
        username.encode("utf-8"), configured_username.encode("utf-8")
    )
    password_matches = hmac.compare_digest(
        password.encode("utf-8"), configured_password.encode("utf-8")
    )
    return username_matches and password_matches


def render_admin(st: Any, config: dict[str, Any]) -> None:
    st.subheader("Admin")
    configured_username = os.environ.get("TREASURE_HUNT_ADMIN_USER", "")
    configured_password = os.environ.get("TREASURE_HUNT_ADMIN_PASSWORD", "")
    if not configured_username or not configured_password:
        st.warning(
            "Admin login is not configured. Set TREASURE_HUNT_ADMIN_USER and "
            "TREASURE_HUNT_ADMIN_PASSWORD on the server, then restart the app."
        )
        return

    if not st.session_state.get("admin_authenticated", False):
        with st.form("admin_login"):
            username = st.text_input("User ID")
            password = st.text_input("Password", type="password")
            login_submitted = st.form_submit_button("Log in", type="primary")
        if login_submitted:
            if verify_admin_credentials(
                username, password, configured_username, configured_password
            ):
                st.session_state.admin_authenticated = True
                st.rerun()
            st.error("Invalid user ID or password.")
        return

    logout_column, _ = st.columns([1, 5])
    if logout_column.button("Log out"):
        st.session_state.admin_authenticated = False
        st.rerun()

    st.caption("Changes apply to all teams and reset current round assignments.")
    config_text = st.text_area(
        "Backend configuration (JSON)",
        value=json.dumps(config, indent=2, ensure_ascii=False),
        height=600,
        key="admin_config_editor",
    )
    if st.button("Save configuration", type="primary"):
        try:
            updated_config = json.loads(config_text)
            validate_config(updated_config)
            if updated_config == config:
                st.info("No configuration changes to save.")
                return
            updated_config["config_revision"] = config.get("config_revision", 0) + 1
            clear_assignments()
            save_config(updated_config)
        except (json.JSONDecodeError, ValueError, OSError, sqlite3.Error) as error:
            st.error(f"Could not save configuration: {error}")
        else:
            st.session_state.pop("admin_config_editor", None)
            st.success("Configuration saved; all round assignments were reset.")
            st.rerun()


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

    section = st.sidebar.radio("Section", ["Game", "Admin"], horizontal=True)
    if section == "Admin":
        render_admin(st, config)
        return

    if "screen" not in st.session_state:
        st.session_state.screen = "team"
    if (
        "assignment_revision" in st.session_state
        and st.session_state.assignment_revision != config.get("config_revision", 0)
    ):
        for key in ("selected_team", "selected_round", "assigned_place_index"):
            st.session_state.pop(key, None)
        st.session_state.pop("assignment_revision", None)
        st.session_state.screen = "team"
    if st.session_state.screen in ("round", "lookup"):
        timer = get_team_timer(st.session_state.selected_team)
        if timer is None or timer[1] is not None:
            st.session_state.screen = "timer"
    if st.session_state.screen in ("timer", "round", "lookup"):
        render_team_timer(st, st.session_state.selected_team)

    if (
        st.session_state.screen == "lookup"
        and "assigned_place_index" not in st.session_state
    ):
        try:
            st.session_state.assigned_place_index = get_or_assign_place(
                st.session_state.selected_team, st.session_state.selected_round
            )
            st.session_state.assignment_revision = config.get("config_revision", 0)
        except (OSError, ValueError, json.JSONDecodeError, sqlite3.Error) as error:
            st.error(f"Could not assign a clue: {error}")
            return

    if st.session_state.screen == "team":
        st.subheader("Select your team")
        with st.form("team_selection"):
            selected_team = st.selectbox("Team", config["teams"])
            continue_to_round = st.form_submit_button("Continue", type="primary")
        if continue_to_round:
            st.session_state.selected_team = selected_team
            st.session_state.screen = "timer"
            st.rerun()
        return

    st.caption(f"Team: {st.session_state.selected_team}")

    if st.session_state.screen == "timer":
        timer = get_team_timer(st.session_state.selected_team)
        if timer is None:
            st.subheader("Ready to start?")
            st.write("The timer will keep running until the final round is completed.")
            if st.button("Start timer", type="primary"):
                try:
                    start_team_timer(st.session_state.selected_team)
                except sqlite3.Error as error:
                    st.error(f"Could not start the timer: {error}")
                else:
                    st.rerun()
        elif timer[1] is not None:
            st.success("This team has completed the treasure hunt.")
            if st.button("Choose another team"):
                st.session_state.screen = "team"
                st.rerun()
        else:
            st.subheader("Timer started")
            st.write("Your timer will continue running if you refresh or leave this page.")
            if st.button("Continue to rounds", type="primary"):
                st.session_state.screen = "round"
                st.rerun()
        return

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
            try:
                st.session_state.assigned_place_index = get_or_assign_place(
                    st.session_state.selected_team, selected_round
                )
                st.session_state.assignment_revision = config.get(
                    "config_revision", 0
                )
            except (OSError, ValueError, json.JSONDecodeError, sqlite3.Error) as error:
                st.error(f"Could not assign a clue: {error}")
                return
            st.session_state.screen = "lookup"
            st.rerun()
        if st.button("Back to team selection"):
            st.session_state.screen = "team"
            st.rerun()
        return

    st.caption(f"Round {st.session_state.selected_round}")
    st.image(
        create_riddle_image(RIDDLES[st.session_state.assigned_place_index]),
        use_container_width=True,
    )
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
            room_number = room_for_assigned_coordinates(
                latitude,
                longitude,
                config["places"],
                st.session_state.assigned_place_index,
                config["match_radius_km"],
                config["room_numbers"],
            )
            if room_number is None:
                st.warning("Those coordinates do not match your clue. Try again.")
            else:
                st.success(f"Room {room_number}")
                if st.session_state.selected_round == max(config["rounds"]):
                    try:
                        finish_team_timer(st.session_state.selected_team)
                    except (ValueError, sqlite3.Error) as error:
                        st.error(f"Could not finish the team timer: {error}")
                    else:
                        st.success("Final round completed!")
        else:
            st.info("Enter the landmark's coordinates to find its room.")


if __name__ == "__main__":
    main()
