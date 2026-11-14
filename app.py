import json
import copy
import hashlib
import hmac
import io
import math
import os
import random
import re
import secrets
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from typing import Any, Optional

from PIL import Image, ImageDraw, ImageFont
from dotenv import load_dotenv

from murder_mysteries import MURDER_MYSTERIES
from riddles import RIDDLES
from sudoku_puzzles import DEFAULT_SUDOKU_PUZZLES


CONFIG_PATH = Path(__file__).with_name("places.json")
ASSIGNMENT_DB_PATH = Path(__file__).with_name("assignments.sqlite3")
LOGO_PATH = Path(__file__).with_name("assets") / "inxiteout_logo.jpg"
load_dotenv(dotenv_path=Path(__file__).with_name(".env"), override=False)
VISUAL_PUZZLE_COUNT = 4
VISUAL_PUZZLE_MAX_POOL_SIZE = 50
VISUAL_PUZZLE_SEGMENT_LENGTH = 16
VISUAL_PUZZLE_FREE_ATTEMPTS = 3
VISUAL_PUZZLE_PENALTY_SECONDS = 120
VISUAL_PUZZLE_MAX_IMAGE_BYTES = 5 * 1024 * 1024
VISUAL_PUZZLE_MAX_IMAGE_PIXELS = 20_000_000
HIDDEN_PHOTOGRAPH_MIN_COUNT = 1
HIDDEN_PHOTOGRAPH_PENALTY_SECONDS = 120
HOTEL_DETECTIVE_FREE_ATTEMPTS = 3
HOTEL_DETECTIVE_PENALTY_SECONDS = 120
MURDER_MYSTERY_CASE_COUNT = 15
MURDER_MYSTERY_FREE_ATTEMPTS = 3
MURDER_MYSTERY_PENALTY_SECONDS = 120
ROUND_PENALTY_DEFAULTS = {
    "2": {
        "free_attempts": HOTEL_DETECTIVE_FREE_ATTEMPTS,
        "penalty_seconds": HOTEL_DETECTIVE_PENALTY_SECONDS,
    },
    "4": {
        "free_attempts": VISUAL_PUZZLE_FREE_ATTEMPTS,
        "penalty_seconds": VISUAL_PUZZLE_PENALTY_SECONDS,
    },
    "5": {"penalty_seconds": HIDDEN_PHOTOGRAPH_PENALTY_SECONDS},
    "6": {
        "free_attempts": MURDER_MYSTERY_FREE_ATTEMPTS,
        "penalty_seconds": MURDER_MYSTERY_PENALTY_SECONDS,
    },
}
GAME_INSTRUCTION_DEFAULTS = {
    "dos": [
        "Keep your team together and follow organizer directions.",
        "Share your team's ideas with one another and submit answers through the app.",
        "Keep track of clues and unlock codes your team earns.",
    ],
    "donts": [
        "Share clues, answers, or unlock codes with other teams.",
        "Interfere with another team's work or try to bypass or alter the game.",
    ],
}
SUDOKU_SIZE = 4
SUDOKU_BOX_SIZE = 2
SUDOKU_CLUE_COUNT = 6
ROUND_TITLES = {
    1: "The Landmark Riddle",
    2: "Hotel Detective: Truth or Trick",
    3: "The Whitepaper Cipher",
    4: "The Image Enigma",
    5: "The Hidden Lens",
    6: "The Final Case: Mystery & Sudoku",
}
ROUND_NUMBERS = list(ROUND_TITLES)


def round_title(round_number: int) -> str:
    title = ROUND_TITLES.get(round_number)
    return f"Round {round_number}: {title}" if title else f"Round {round_number}"


def admin_round_access_enabled(admin_authenticated: bool) -> bool:
    return (
        bool(admin_authenticated)
        and os.environ.get("environment", "").strip().casefold() == "local"
    )


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
    room_numbers: Optional[list[str]] = None,
) -> Optional[str]:
    nearest = find_nearest_place(latitude, longitude, places)
    if nearest is None:
        return None

    place_index, _, _, distance = nearest
    if place_index != assigned_place_index or distance > match_radius_km:
        return None
    place_room_number = places[place_index].get("room_number")
    if place_room_number is not None:
        return str(place_room_number)
    if room_numbers is None or room_group_index(place_index) >= len(room_numbers):
        raise ValueError("No room number is configured for the matched place.")
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
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS team_logins (
            team TEXT PRIMARY KEY,
            browser_id TEXT NOT NULL UNIQUE,
            logged_in_at REAL NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS team_round_timers (
            team TEXT NOT NULL,
            round_number INTEGER NOT NULL,
            started_at REAL NOT NULL,
            finished_at REAL,
            PRIMARY KEY (team, round_number)
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS team_round_unlock_codes (
            team TEXT NOT NULL,
            round_number INTEGER NOT NULL,
            code TEXT NOT NULL,
            entered_at REAL,
            PRIMARY KEY (team, round_number)
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS timer_freezes (
            team TEXT,
            round_number INTEGER,
            started_at REAL NOT NULL,
            finished_at REAL,
            CHECK ((team IS NULL AND round_number IS NULL) OR
                   (team IS NOT NULL AND round_number IS NOT NULL))
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS team_reset_revisions (
            team TEXT PRIMARY KEY,
            revision INTEGER NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS hotel_statements (
            position INTEGER PRIMARY KEY,
            statement TEXT NOT NULL,
            is_true INTEGER NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS hotel_set_codes (
            set_number INTEGER PRIMARY KEY,
            code TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS hotel_detective_assignments (
            team TEXT PRIMARY KEY,
            set_number INTEGER NOT NULL CHECK (set_number BETWEEN 1 AND 4)
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS hotel_detective_progress (
            team TEXT NOT NULL,
            set_number INTEGER NOT NULL,
            wrong_attempts INTEGER NOT NULL DEFAULT 0,
            penalty_seconds INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (team, set_number)
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS whitepaper_questions (
            position INTEGER PRIMARY KEY,
            question TEXT NOT NULL,
            answer TEXT NOT NULL,
            code TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS whitepaper_set_codes (
            set_number INTEGER PRIMARY KEY,
            code TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS whitepaper_assignments (
            team TEXT PRIMARY KEY,
            set_number INTEGER NOT NULL CHECK (set_number BETWEEN 1 AND 4)
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS visual_puzzles (
            position INTEGER PRIMARY KEY,
            image BLOB NOT NULL,
            answer TEXT NOT NULL,
            code_segment TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS visual_puzzle_progress (
            team TEXT PRIMARY KEY,
            current_position INTEGER NOT NULL DEFAULT 1,
            wrong_attempts INTEGER NOT NULL DEFAULT 0,
            penalty_seconds INTEGER NOT NULL DEFAULT 0,
            completed INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS visual_puzzle_assignments (
            team TEXT NOT NULL,
            sequence_position INTEGER NOT NULL,
            puzzle_position INTEGER NOT NULL,
            PRIMARY KEY (team, sequence_position),
            UNIQUE (team, puzzle_position),
            FOREIGN KEY (team) REFERENCES visual_puzzle_progress (team),
            FOREIGN KEY (puzzle_position) REFERENCES visual_puzzles (position)
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS hidden_photographs (
            position INTEGER PRIMARY KEY,
            image BLOB NOT NULL,
            code TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS hidden_photograph_assignments (
            team TEXT PRIMARY KEY,
            photograph_position INTEGER NOT NULL,
            FOREIGN KEY (photograph_position)
                REFERENCES hidden_photographs (position)
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS hidden_photograph_submissions (
            team TEXT PRIMARY KEY,
            image BLOB NOT NULL,
            submitted_at REAL NOT NULL,
            reviewed_at REAL,
            is_correct INTEGER,
            penalty_seconds INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY (team)
                REFERENCES hidden_photograph_assignments (team)
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS murder_mystery_assignments (
            team TEXT PRIMARY KEY,
            case_position INTEGER NOT NULL UNIQUE
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS murder_mystery_content (
            position INTEGER PRIMARY KEY,
            title TEXT NOT NULL,
            briefing TEXT NOT NULL,
            characters_json TEXT NOT NULL,
            evidence_json TEXT NOT NULL,
            culprit TEXT NOT NULL,
            word_puzzle TEXT NOT NULL DEFAULT '',
            word_answer TEXT NOT NULL DEFAULT '',
            sudoku_puzzle TEXT NOT NULL DEFAULT '',
            sudoku_solution TEXT NOT NULL DEFAULT '',
            sudoku_size INTEGER NOT NULL DEFAULT 4
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS murder_mystery_progress (
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
    content_columns = {
        row[1]
        for row in connection.execute("PRAGMA table_info(murder_mystery_content)")
    }
    content_migrations = (
        ("sudoku_puzzle", "TEXT NOT NULL DEFAULT ''"),
        ("sudoku_solution", "TEXT NOT NULL DEFAULT ''"),
        ("sudoku_size", "INTEGER NOT NULL DEFAULT 9"),
    )
    for column, definition in content_migrations:
        if column not in content_columns:
            connection.execute(
                f"ALTER TABLE murder_mystery_content ADD COLUMN "
                f"{column} {definition}"
            )
            content_columns.add(column)
    progress_columns = {
        row[1]
        for row in connection.execute("PRAGMA table_info(murder_mystery_progress)")
    }
    for column in ("sudoku_attempts", "sudoku_solved"):
        if column not in progress_columns:
            connection.execute(
                f"ALTER TABLE murder_mystery_progress ADD COLUMN "
                f"{column} INTEGER NOT NULL DEFAULT 0"
            )
    assignment_indexes = connection.execute(
        "PRAGMA index_list(hidden_photograph_assignments)"
    ).fetchall()
    has_unique_photo_position = any(
        index[2]
        and [
            column[2]
            for column in connection.execute(
                f'PRAGMA index_info("{index[1].replace(chr(34), chr(34) * 2)}")'
            )
        ] == ["photograph_position"]
        for index in assignment_indexes
    )
    if has_unique_photo_position:
        connection.execute(
            """
            CREATE TABLE hidden_photograph_assignments_new (
                team TEXT PRIMARY KEY,
                photograph_position INTEGER NOT NULL,
                FOREIGN KEY (photograph_position)
                    REFERENCES hidden_photographs (position)
            )
            """
        )
        connection.execute(
            """
            INSERT INTO hidden_photograph_assignments_new (team, photograph_position)
            SELECT team, photograph_position FROM hidden_photograph_assignments
            """
        )
        connection.execute(
            """
            CREATE TABLE hidden_photograph_submissions_new (
                team TEXT PRIMARY KEY,
                image BLOB NOT NULL,
                submitted_at REAL NOT NULL,
                reviewed_at REAL,
                is_correct INTEGER,
                penalty_seconds INTEGER NOT NULL DEFAULT 0,
                FOREIGN KEY (team)
                    REFERENCES hidden_photograph_assignments_new (team)
            )
            """
        )
        connection.execute(
            """
            INSERT INTO hidden_photograph_submissions_new
                (team, image, submitted_at, reviewed_at, is_correct, penalty_seconds)
            SELECT team, image, submitted_at, reviewed_at, is_correct, penalty_seconds
            FROM hidden_photograph_submissions
            """
        )
        connection.execute("DROP TABLE hidden_photograph_submissions")
        connection.execute("DROP TABLE hidden_photograph_assignments")
        connection.execute(
            "ALTER TABLE hidden_photograph_assignments_new "
            "RENAME TO hidden_photograph_assignments"
        )
        connection.execute(
            "ALTER TABLE hidden_photograph_submissions_new "
            "RENAME TO hidden_photograph_submissions"
        )


def load_hotel_detective_content() -> tuple[list[dict[str, Any]], list[str]]:
    statements = [
        {"Statement": "", "Correct answer": ""}
        for _ in range(20)
    ]
    codes = [""] * 4
    with closing(sqlite3.connect(ASSIGNMENT_DB_PATH, timeout=30)) as connection, connection:
        create_database_tables(connection)
        for position, statement, is_true in connection.execute(
            "SELECT position, statement, is_true FROM hotel_statements"
        ):
            statements[position - 1] = {
                "Statement": statement,
                "Correct answer": "True" if is_true else "False",
            }
        for set_number, code in connection.execute(
            "SELECT set_number, code FROM hotel_set_codes"
        ):
            codes[set_number - 1] = code
    return statements, codes


def save_hotel_detective_content(
    statements: list[dict[str, Any]], codes: list[str]
) -> None:
    if len(statements) != 20 or len(codes) != 4:
        raise ValueError("Hotel Detective requires 20 statement slots and 4 codes.")

    normalized_statements: list[tuple[int, str, int]] = []
    for position, row in enumerate(statements, start=1):
        statement_value = row.get("Statement")
        statement_is_empty = statement_value is None or (
            isinstance(statement_value, float) and math.isnan(statement_value)
        )
        statement = (
            "" if statement_is_empty else str(statement_value).strip()
        )
        answer = row.get("Correct answer")
        if answer is None or (
            isinstance(answer, float) and math.isnan(answer)
        ):
            answer = ""
        if not statement:
            continue
        if len(statement) > 500:
            raise ValueError("Each statement must be 500 characters or fewer.")
        if not (
            answer is True
            or answer is False
            or answer == "True"
            or answer == "False"
        ):
            raise ValueError(
                f"Statement {position} needs a True or False correct answer."
            )
        normalized_statements.append(
            (position, statement, int(answer is True or answer == "True"))
        )

    normalized_codes = [
        ""
        if code is None or (isinstance(code, float) and math.isnan(code))
        else str(code).strip()
        for code in codes
    ]
    if any(len(code) > 100 for code in normalized_codes):
        raise ValueError("Each set code must be 100 characters or fewer.")

    with closing(sqlite3.connect(ASSIGNMENT_DB_PATH, timeout=30)) as connection, connection:
        connection.execute("BEGIN IMMEDIATE")
        create_database_tables(connection)
        connection.execute("DELETE FROM hotel_statements")
        connection.executemany(
            """
            INSERT INTO hotel_statements (position, statement, is_true)
            VALUES (?, ?, ?)
            """,
            normalized_statements,
        )
        connection.execute("DELETE FROM hotel_set_codes")
        connection.executemany(
            "INSERT INTO hotel_set_codes (set_number, code) VALUES (?, ?)",
            [
                (set_number, code)
                for set_number, code in enumerate(normalized_codes, start=1)
                if code
            ],
        )


def check_hotel_detective_answers(
    set_number: int, answers: list[bool]
) -> tuple[bool, Optional[str]]:
    if (
        isinstance(set_number, bool)
        or not isinstance(set_number, int)
        or set_number not in range(1, 5)
    ):
        raise ValueError("Set number must be between 1 and 4.")
    if len(answers) != 5 or not all(isinstance(answer, bool) for answer in answers):
        raise ValueError("Choose True or False for all five statements.")

    first_position = (set_number - 1) * 5 + 1
    with closing(sqlite3.connect(ASSIGNMENT_DB_PATH, timeout=30)) as connection, connection:
        create_database_tables(connection)
        rows = connection.execute(
            """
            SELECT is_true FROM hotel_statements
            WHERE position BETWEEN ? AND ?
            ORDER BY position
            """,
            (first_position, first_position + 4),
        ).fetchall()
        code_row = connection.execute(
            "SELECT code FROM hotel_set_codes WHERE set_number = ?",
            (set_number,),
        ).fetchone()

    if len(rows) != 5:
        raise ValueError(f"Set {set_number} is not ready yet.")
    is_correct = all(bool(row[0]) == answer for row, answer in zip(rows, answers))
    return is_correct, code_row[0] if is_correct and code_row is not None else None


def get_hotel_detective_progress(team: str, set_number: int) -> dict[str, int]:
    if (
        isinstance(set_number, bool)
        or not isinstance(set_number, int)
        or set_number not in range(1, 5)
    ):
        raise ValueError("Set number must be between 1 and 4.")
    with closing(sqlite3.connect(ASSIGNMENT_DB_PATH, timeout=30)) as connection, connection:
        create_database_tables(connection)
        row = connection.execute(
            """
            SELECT wrong_attempts, penalty_seconds
            FROM hotel_detective_progress
            WHERE team = ? AND set_number = ?
            """,
            (team, set_number),
        ).fetchone()
    return {
        "wrong_attempts": row[0] if row is not None else 0,
        "penalty_seconds": row[1] if row is not None else 0,
    }


def get_or_assign_hotel_detective_set(
    team: str, available_sets: list[int]
) -> int:
    if not team.strip() or not available_sets or any(
        isinstance(set_number, bool)
        or not isinstance(set_number, int)
        or set_number not in range(1, 5)
        for set_number in available_sets
    ):
        raise ValueError(
            "A team and at least one valid Hotel Detective set are required."
        )

    available_sets = list(dict.fromkeys(available_sets))
    with (
        closing(sqlite3.connect(ASSIGNMENT_DB_PATH, timeout=30)) as connection,
        connection,
    ):
        connection.execute("BEGIN IMMEDIATE")
        create_database_tables(connection)
        assignment = connection.execute(
            "SELECT set_number FROM hotel_detective_assignments WHERE team = ?",
            (team,),
        ).fetchone()
        if assignment is not None and assignment[0] in available_sets:
            return assignment[0]

        set_number = random.choice(available_sets)
        connection.execute(
            """
            INSERT INTO hotel_detective_assignments (team, set_number)
            VALUES (?, ?)
            ON CONFLICT(team) DO UPDATE SET set_number = excluded.set_number
            """,
            (team, set_number),
        )
        return set_number


def submit_hotel_detective_answers(
    team: str, set_number: int, answers: list[bool]
) -> dict[str, Any]:
    is_correct, unlock_code = check_hotel_detective_answers(set_number, answers)
    if is_correct:
        progress = get_hotel_detective_progress(team, set_number)
        return {
            "is_correct": True,
            "unlock_code": unlock_code,
            **progress,
            "added_penalty": 0,
        }

    penalty_settings = load_config()["round_penalties"]["2"]
    with closing(sqlite3.connect(ASSIGNMENT_DB_PATH, timeout=30)) as connection, connection:
        connection.execute("BEGIN IMMEDIATE")
        create_database_tables(connection)
        connection.execute(
            """
            INSERT OR IGNORE INTO hotel_detective_progress (team, set_number)
            VALUES (?, ?)
            """,
            (team, set_number),
        )
        progress = connection.execute(
            """
            SELECT wrong_attempts, penalty_seconds
            FROM hotel_detective_progress
            WHERE team = ? AND set_number = ?
            """,
            (team, set_number),
        ).fetchone()
        wrong_attempts = progress[0] + 1
        added_penalty = (
            penalty_settings["penalty_seconds"]
            if wrong_attempts > penalty_settings["free_attempts"]
            else 0
        )
        penalty_seconds = progress[1] + added_penalty
        connection.execute(
            """
            UPDATE hotel_detective_progress
            SET wrong_attempts = ?, penalty_seconds = ?
            WHERE team = ? AND set_number = ?
            """,
            (wrong_attempts, penalty_seconds, team, set_number),
        )
    return {
        "is_correct": False,
        "unlock_code": None,
        "wrong_attempts": wrong_attempts,
        "penalty_seconds": penalty_seconds,
        "added_penalty": added_penalty,
    }


def load_whitepaper_content() -> tuple[list[dict[str, str]], list[str]]:
    questions = [{"Question": "", "Correct answer": ""} for _ in range(16)]
    codes = [""] * 4
    with closing(sqlite3.connect(ASSIGNMENT_DB_PATH, timeout=30)) as connection, connection:
        create_database_tables(connection)
        for set_number in range(1, 5):
            first_position = (set_number - 1) * 4 + 1
            legacy_code = connection.execute(
                """
                SELECT code FROM whitepaper_questions
                WHERE position BETWEEN ? AND ? AND trim(code) != ''
                ORDER BY position LIMIT 1
                """,
                (first_position, first_position + 3),
            ).fetchone()
            if legacy_code is not None:
                connection.execute(
                    """
                    INSERT OR IGNORE INTO whitepaper_set_codes (set_number, code)
                    VALUES (?, ?)
                    """,
                    (set_number, legacy_code[0]),
                )
        for position, question, answer in connection.execute(
            """
            SELECT position, question, answer FROM whitepaper_questions
            WHERE position BETWEEN 1 AND 16
            """
        ):
            questions[position - 1] = {
                "Question": question,
                "Correct answer": answer,
            }
        for set_number, code in connection.execute(
            "SELECT set_number, code FROM whitepaper_set_codes"
        ):
            codes[set_number - 1] = code
    return questions, codes


def get_or_assign_whitepaper_set(team: str, available_sets: list[int]) -> int:
    if not team.strip() or not available_sets or any(
        isinstance(set_number, bool)
        or not isinstance(set_number, int)
        or set_number not in range(1, 5)
        for set_number in available_sets
    ):
        raise ValueError("A team and at least one valid whitepaper set are required.")

    available_sets = list(dict.fromkeys(available_sets))
    with (
        closing(sqlite3.connect(ASSIGNMENT_DB_PATH, timeout=30)) as connection,
        connection,
    ):
        connection.execute("BEGIN IMMEDIATE")
        create_database_tables(connection)
        assignment = connection.execute(
            "SELECT set_number FROM whitepaper_assignments WHERE team = ?",
            (team,),
        ).fetchone()
        if assignment is not None and assignment[0] in available_sets:
            return assignment[0]

        set_number = random.choice(available_sets)
        connection.execute(
            """
            INSERT INTO whitepaper_assignments (team, set_number)
            VALUES (?, ?)
            ON CONFLICT(team) DO UPDATE SET set_number = excluded.set_number
            """,
            (team, set_number),
        )
        return set_number


def save_whitepaper_content(
    questions: list[dict[str, Any]], codes: list[str]
) -> None:
    if len(questions) != 16 or len(codes) != 4:
        raise ValueError("Round 3 requires 16 questions and 4 set unlock codes.")

    normalized_questions: list[tuple[int, str, str, str]] = []
    for position, row in enumerate(questions, start=1):
        values = []
        for field in ("Question", "Correct answer"):
            value = row.get(field)
            if value is None or (isinstance(value, float) and math.isnan(value)):
                value = ""
            values.append(str(value).strip())
        question, answer = values
        if not question or not answer:
            raise ValueError(f"Question {position} needs a question and correct answer.")
        if len(question) > 500:
            raise ValueError("Each question must be 500 characters or fewer.")
        if len(answer) > 200:
            raise ValueError("Each answer must be 200 characters or fewer.")
        normalized_questions.append((position, question, answer, ""))

    normalized_codes = []
    for set_number, code in enumerate(codes, start=1):
        if code is None or (isinstance(code, float) and math.isnan(code)):
            normalized_code = ""
        else:
            normalized_code = str(code).strip()
        if not normalized_code:
            raise ValueError(f"Set {set_number} needs an unlock code.")
        if len(normalized_code) > 100:
            raise ValueError("Each set unlock code must be 100 characters or fewer.")
        normalized_codes.append((set_number, normalized_code))

    with closing(sqlite3.connect(ASSIGNMENT_DB_PATH, timeout=30)) as connection, connection:
        connection.execute("BEGIN IMMEDIATE")
        create_database_tables(connection)
        connection.execute("DELETE FROM whitepaper_questions")
        connection.executemany(
            """
            INSERT INTO whitepaper_questions (position, question, answer, code)
            VALUES (?, ?, ?, ?)
            """,
            normalized_questions,
        )
        connection.execute("DELETE FROM whitepaper_set_codes")
        connection.executemany(
            """
            INSERT INTO whitepaper_set_codes (set_number, code)
            VALUES (?, ?)
            """,
            normalized_codes,
        )


def check_whitepaper_set_answers(
    set_number: int, answers: list[str]
) -> tuple[bool, Optional[str]]:
    if (
        isinstance(set_number, bool)
        or not isinstance(set_number, int)
        or set_number not in range(1, 5)
    ):
        raise ValueError("Set number must be between 1 and 4.")
    if (
        not isinstance(answers, list)
        or len(answers) != 4
        or any(not isinstance(answer, str) or not answer.strip() for answer in answers)
    ):
        raise ValueError("Enter an answer for all four questions.")

    first_position = (set_number - 1) * 4 + 1
    with closing(sqlite3.connect(ASSIGNMENT_DB_PATH, timeout=30)) as connection, connection:
        create_database_tables(connection)
        rows = connection.execute(
            """
            SELECT answer FROM whitepaper_questions
            WHERE position BETWEEN ? AND ?
            ORDER BY position
            """,
            (first_position, first_position + 3),
        ).fetchall()
        code_row = connection.execute(
            "SELECT code FROM whitepaper_set_codes WHERE set_number = ?",
            (set_number,),
        ).fetchone()
    if len(rows) != 4 or code_row is None:
        raise ValueError(f"Set {set_number} is not ready yet.")

    normalize = lambda value: " ".join(value.split()).casefold().encode("utf-8")
    is_correct = all(
        hmac.compare_digest(normalize(answer), normalize(row[0]))
        for answer, row in zip(answers, rows)
    )
    return is_correct, code_row[0] if is_correct else None


def validate_uploaded_image(image_data: bytes, description: str) -> bytes:
    if not isinstance(image_data, bytes) or not image_data:
        raise ValueError(f"{description} needs an image.")
    if len(image_data) > VISUAL_PUZZLE_MAX_IMAGE_BYTES:
        raise ValueError("Each uploaded image must be 5 MB or smaller.")

    try:
        with Image.open(io.BytesIO(image_data)) as image:
            if image.format not in ("JPEG", "PNG", "WEBP"):
                raise ValueError("Images must be JPEG, PNG, or WebP files.")
            if image.width * image.height > VISUAL_PUZZLE_MAX_IMAGE_PIXELS:
                raise ValueError("Each image must contain 20 megapixels or fewer.")
            image.verify()
    except (OSError, Image.DecompressionBombError) as error:
        raise ValueError("The uploaded file is not a valid supported image.") from error
    return image_data


def validate_visual_puzzle_image(image_data: bytes) -> bytes:
    return validate_uploaded_image(image_data, "Each visual puzzle")


def load_visual_puzzle_content() -> list[dict[str, Any]]:
    with closing(sqlite3.connect(ASSIGNMENT_DB_PATH, timeout=30)) as connection, connection:
        create_database_tables(connection)
        rows = connection.execute(
            """
            SELECT position, image, answer, code_segment
            FROM visual_puzzles ORDER BY position
            """
        ).fetchall()
    return [
        {
            "Position": position,
            "Image": image,
            "Correct answer": answer,
            "Code segment": code_segment,
        }
        for position, image, answer, code_segment in rows
    ]


def save_visual_puzzle_content(puzzles: list[dict[str, Any]]) -> None:
    if not VISUAL_PUZZLE_COUNT <= len(puzzles) <= VISUAL_PUZZLE_MAX_POOL_SIZE:
        raise ValueError(
            f"Round 4 requires between {VISUAL_PUZZLE_COUNT} and "
            f"{VISUAL_PUZZLE_MAX_POOL_SIZE} visual puzzles."
        )

    normalized_puzzles: list[tuple[int, bytes, str, str]] = []
    for position, row in enumerate(puzzles, start=1):
        image_data = row.get("Image")
        answer_value = row.get("Correct answer")
        segment_value = row.get("Code segment")
        answer = "" if answer_value is None else str(answer_value).strip()
        segment = "" if segment_value is None else str(segment_value).strip()
        if not image_data or not answer or not segment:
            raise ValueError(
                f"Puzzle {position} needs an image, correct answer, and 16-digit code segment."
            )
        image = validate_visual_puzzle_image(image_data)
        if len(answer) > 200:
            raise ValueError("Each correct answer must be 200 characters or fewer.")
        if not re.fullmatch(r"[0-9]{16}", segment):
            raise ValueError("Each code segment must contain exactly 16 digits.")
        normalized_puzzles.append((position, image, answer, segment))

    if len(normalized_puzzles) != len(puzzles):
        raise ValueError("Every visual puzzle needs an image, answer, and code segment.")

    with closing(sqlite3.connect(ASSIGNMENT_DB_PATH, timeout=30)) as connection, connection:
        connection.execute("BEGIN IMMEDIATE")
        create_database_tables(connection)
        connection.execute("DELETE FROM visual_puzzle_assignments")
        connection.execute("DELETE FROM visual_puzzles")
        connection.executemany(
            """
            INSERT INTO visual_puzzles (position, image, answer, code_segment)
            VALUES (?, ?, ?, ?)
            """,
            normalized_puzzles,
        )
        connection.execute("DELETE FROM visual_puzzle_progress")


def load_hidden_photograph_content() -> list[dict[str, Any]]:
    with closing(sqlite3.connect(ASSIGNMENT_DB_PATH, timeout=30)) as connection, connection:
        create_database_tables(connection)
        rows = connection.execute(
            """
            SELECT position, image, code
            FROM hidden_photographs ORDER BY position
            """
        ).fetchall()
    return [
        {"Position": position, "Image": image, "Code": code}
        for position, image, code in rows
    ]


def save_hidden_photograph_content(
    photographs: list[dict[str, Any]],
    minimum_count: int = HIDDEN_PHOTOGRAPH_MIN_COUNT,
) -> None:
    if len(photographs) < minimum_count:
        raise ValueError(
            f"Round 5 requires at least {minimum_count} photographs."
        )

    normalized_photographs: list[tuple[int, bytes, str]] = []
    for position, row in enumerate(photographs, start=1):
        image_data = row.get("Image")
        code_value = row.get("Code")
        code = "" if code_value is None else str(code_value).strip()
        if not image_data or not code:
            raise ValueError(
                f"Photograph {position} needs an image and a code."
            )
        image = validate_uploaded_image(image_data, f"Photograph {position}")
        if len(code) > 200:
            raise ValueError("Each revealed code must be 200 characters or fewer.")
        normalized_photographs.append((position, image, code))

    with closing(sqlite3.connect(ASSIGNMENT_DB_PATH, timeout=30)) as connection, connection:
        connection.execute("BEGIN IMMEDIATE")
        create_database_tables(connection)
        connection.execute("DELETE FROM hidden_photograph_submissions")
        connection.execute("DELETE FROM hidden_photograph_assignments")
        connection.execute("DELETE FROM hidden_photographs")
        connection.executemany(
            """
            INSERT INTO hidden_photographs (position, image, code)
            VALUES (?, ?, ?)
            """,
            normalized_photographs,
        )


def ensure_hidden_photograph_assignment(
    connection: sqlite3.Connection, team: str
) -> int:
    available_positions = [
        row[0]
        for row in connection.execute(
            "SELECT position FROM hidden_photographs ORDER BY position"
        )
    ]
    if not available_positions:
        raise ValueError(
            "Round 5 needs at least one configured photograph. "
            "Please ask an admin to configure it."
        )
    assignment = connection.execute(
        """
        SELECT photograph_position FROM hidden_photograph_assignments
        WHERE team = ?
        """,
        (team,),
    ).fetchone()
    if assignment is not None:
        return assignment[0]

    used_positions = {
        row[0]
        for row in connection.execute(
            "SELECT photograph_position FROM hidden_photograph_assignments"
        )
    }
    unused_positions = [
        position for position in available_positions if position not in used_positions
    ]
    position = random.choice(unused_positions or available_positions)
    connection.execute(
        """
        INSERT INTO hidden_photograph_assignments (team, photograph_position)
        VALUES (?, ?)
        """,
        (team, position),
    )
    return position


def get_hidden_photograph_state(team: str) -> dict[str, Any]:
    with closing(sqlite3.connect(ASSIGNMENT_DB_PATH, timeout=30)) as connection, connection:
        connection.execute("BEGIN IMMEDIATE")
        create_database_tables(connection)
        position = ensure_hidden_photograph_assignment(connection, team)
        photograph = connection.execute(
            "SELECT image, code FROM hidden_photographs WHERE position = ?",
            (position,),
        ).fetchone()
        submission = connection.execute(
            """
            SELECT image, reviewed_at, is_correct, penalty_seconds
            FROM hidden_photograph_submissions WHERE team = ?
            """,
            (team,),
        ).fetchone()
    return {
        "photograph_position": position,
        "reference_image": photograph[0],
        "code": photograph[1] if submission is not None else None,
        "submission_image": submission[0] if submission is not None else None,
        "reviewed": submission is not None and submission[1] is not None,
        "is_correct": (
            bool(submission[2])
            if submission is not None and submission[2] is not None
            else None
        ),
        "penalty_seconds": submission[3] if submission is not None else 0,
    }


def submit_hidden_photograph(team: str, image_data: bytes) -> str:
    image = validate_uploaded_image(image_data, "Your Round 5 submission")
    with closing(sqlite3.connect(ASSIGNMENT_DB_PATH, timeout=30)) as connection, connection:
        connection.execute("BEGIN IMMEDIATE")
        create_database_tables(connection)
        position = ensure_hidden_photograph_assignment(connection, team)
        existing_submission = connection.execute(
            """
            SELECT reviewed_at FROM hidden_photograph_submissions
            WHERE team = ?
            """,
            (team,),
        ).fetchone()
        if existing_submission is not None and existing_submission[0] is not None:
            raise ValueError("This photograph has already been reviewed.")
        code = connection.execute(
            "SELECT code FROM hidden_photographs WHERE position = ?",
            (position,),
        ).fetchone()[0]
        connection.execute(
            """
            INSERT INTO hidden_photograph_submissions
                (team, image, submitted_at, reviewed_at, is_correct, penalty_seconds)
            VALUES (?, ?, ?, NULL, NULL, 0)
            ON CONFLICT(team) DO UPDATE SET
                image = excluded.image,
                submitted_at = excluded.submitted_at,
                reviewed_at = NULL,
                is_correct = NULL,
                penalty_seconds = 0
            """,
            (team, image, time.time()),
        )
    return code


def load_hidden_photograph_submissions() -> list[dict[str, Any]]:
    with closing(sqlite3.connect(ASSIGNMENT_DB_PATH, timeout=30)) as connection, connection:
        create_database_tables(connection)
        rows = connection.execute(
            """
            SELECT submissions.team, assignments.photograph_position,
                photographs.image, submissions.image, submissions.submitted_at,
                submissions.reviewed_at, submissions.is_correct,
                submissions.penalty_seconds
            FROM hidden_photograph_submissions submissions
            JOIN hidden_photograph_assignments assignments
                ON assignments.team = submissions.team
            JOIN hidden_photographs photographs
                ON photographs.position = assignments.photograph_position
            ORDER BY submissions.submitted_at, submissions.team
            """
        ).fetchall()
    return [
        {
            "team": row[0],
            "photograph_position": row[1],
            "reference_image": row[2],
            "submission_image": row[3],
            "submitted_at": row[4],
            "reviewed_at": row[5],
            "is_correct": bool(row[6]) if row[6] is not None else None,
            "penalty_seconds": row[7],
        }
        for row in rows
    ]


def review_hidden_photograph_submission(team: str, is_correct: bool) -> int:
    if not isinstance(is_correct, bool):
        raise ValueError("Choose whether the submitted photograph is correct.")
    penalty_seconds = load_config()["round_penalties"]["5"]["penalty_seconds"]
    with closing(sqlite3.connect(ASSIGNMENT_DB_PATH, timeout=30)) as connection, connection:
        connection.execute("BEGIN IMMEDIATE")
        create_database_tables(connection)
        submission = connection.execute(
            """
            SELECT reviewed_at FROM hidden_photograph_submissions WHERE team = ?
            """,
            (team,),
        ).fetchone()
        if submission is None:
            raise ValueError("This team has no Round 5 photograph to review.")
        if submission[0] is not None:
            raise ValueError("This Round 5 photograph has already been reviewed.")
        applied_penalty = 0 if is_correct else penalty_seconds
        connection.execute(
            """
            UPDATE hidden_photograph_submissions
            SET reviewed_at = ?, is_correct = ?, penalty_seconds = ?
            WHERE team = ?
            """,
            (time.time(), int(is_correct), applied_penalty, team),
        )
    return applied_penalty


def normalize_sudoku_grid(grid: str, label: str, allow_blanks: bool) -> str:
    if not isinstance(grid, str):
        raise ValueError(f"{label} must be entered as four rows of digits.")
    rows = [line.strip() for line in grid.strip().splitlines() if line.strip()]
    digit_pattern = (
        rf"[0-{SUDOKU_SIZE}]{{{SUDOKU_SIZE}}}"
        if allow_blanks
        else rf"[1-{SUDOKU_SIZE}]{{{SUDOKU_SIZE}}}"
    )
    if len(rows) != SUDOKU_SIZE or any(
        not re.fullmatch(digit_pattern, row) for row in rows
    ):
        raise ValueError(
            f"{label} must contain four rows of four digits."
        )
    return "".join(rows)


def is_valid_sudoku_solution(solution: str) -> bool:
    required_digits = set(str(digit) for digit in range(1, SUDOKU_SIZE + 1))
    rows = [
        solution[index : index + SUDOKU_SIZE]
        for index in range(0, SUDOKU_SIZE**2, SUDOKU_SIZE)
    ]
    columns = [
        "".join(rows[row][column] for row in range(SUDOKU_SIZE))
        for column in range(SUDOKU_SIZE)
    ]
    boxes = [
        "".join(
            rows[row][column]
            for row in range(box_row, box_row + SUDOKU_BOX_SIZE)
            for column in range(box_column, box_column + SUDOKU_BOX_SIZE)
        )
        for box_row in range(0, SUDOKU_SIZE, SUDOKU_BOX_SIZE)
        for box_column in range(0, SUDOKU_SIZE, SUDOKU_BOX_SIZE)
    ]
    return all(
        set(group) == required_digits
        for group in rows + columns + boxes
    )


def count_sudoku_solutions(puzzle: str, limit: int = 2) -> int:
    board = [int(digit) for digit in puzzle]
    row_masks = [0] * SUDOKU_SIZE
    column_masks = [0] * SUDOKU_SIZE
    box_masks = [0] * SUDOKU_SIZE
    full_mask = (1 << SUDOKU_SIZE) - 1

    for index, value in enumerate(board):
        if value == 0:
            continue
        row, column = divmod(index, SUDOKU_SIZE)
        box = (
            (row // SUDOKU_BOX_SIZE) * SUDOKU_BOX_SIZE
            + column // SUDOKU_BOX_SIZE
        )
        bit = 1 << (value - 1)
        if row_masks[row] & bit or column_masks[column] & bit or box_masks[box] & bit:
            return 0
        row_masks[row] |= bit
        column_masks[column] |= bit
        box_masks[box] |= bit

    def solve() -> int:
        selected_index = -1
        selected_options = 0
        fewest_options = 10
        for index, value in enumerate(board):
            if value:
                continue
            row, column = divmod(index, SUDOKU_SIZE)
            box = (
                (row // SUDOKU_BOX_SIZE) * SUDOKU_BOX_SIZE
                + column // SUDOKU_BOX_SIZE
            )
            options = full_mask & ~(
                row_masks[row] | column_masks[column] | box_masks[box]
            )
            option_count = bin(options).count("1")
            if option_count == 0:
                return 0
            if option_count < fewest_options:
                selected_index = index
                selected_options = options
                fewest_options = option_count
                if option_count == 1:
                    break

        if selected_index == -1:
            return 1

        row, column = divmod(selected_index, SUDOKU_SIZE)
        box = (
            (row // SUDOKU_BOX_SIZE) * SUDOKU_BOX_SIZE
            + column // SUDOKU_BOX_SIZE
        )
        solution_count = 0
        while selected_options:
            bit = selected_options & -selected_options
            selected_options ^= bit
            value = bit.bit_length()
            board[selected_index] = value
            row_masks[row] |= bit
            column_masks[column] |= bit
            box_masks[box] |= bit
            solution_count += solve()
            board[selected_index] = 0
            row_masks[row] ^= bit
            column_masks[column] ^= bit
            box_masks[box] ^= bit
            if solution_count >= limit:
                return solution_count
        return solution_count

    return solve()


def validate_sudoku_case(puzzle_text: str, solution_text: str) -> tuple[str, str]:
    puzzle = normalize_sudoku_grid(puzzle_text, "Sudoku puzzle", allow_blanks=True)
    solution = normalize_sudoku_grid(
        solution_text, "Sudoku solution", allow_blanks=False
    )
    if not is_valid_sudoku_solution(solution):
        raise ValueError("The Sudoku solution must be valid in every row, column, and box.")
    if any(given != "0" and given != solved for given, solved in zip(puzzle, solution)):
        raise ValueError("The Sudoku solution must preserve all puzzle clues.")
    empty_cells = puzzle.count("0")
    if empty_cells != SUDOKU_SIZE**2 - SUDOKU_CLUE_COUNT:
        raise ValueError(
            f"Each hard 4x4 Sudoku must have exactly {SUDOKU_CLUE_COUNT} clues."
        )
    if count_sudoku_solutions(puzzle) != 1:
        raise ValueError("Each Sudoku puzzle must have exactly one solution.")
    return puzzle, solution


def ensure_murder_mystery_content(connection: sqlite3.Connection) -> None:
    count = connection.execute(
        "SELECT COUNT(*) FROM murder_mystery_content"
    ).fetchone()[0]
    if count == 0:
        connection.executemany(
            """
            INSERT INTO murder_mystery_content (
                position, title, briefing, characters_json, evidence_json,
                culprit, word_puzzle, word_answer, sudoku_puzzle,
                sudoku_solution, sudoku_size
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    position,
                    case["title"],
                    case["briefing"],
                    json.dumps(case["characters"], ensure_ascii=False),
                    json.dumps(case["evidence"], ensure_ascii=False),
                    case["culprit"],
                    "",
                    "",
                    DEFAULT_SUDOKU_PUZZLES[position - 1]["puzzle"],
                    DEFAULT_SUDOKU_PUZZLES[position - 1]["solution"],
                    SUDOKU_SIZE,
                )
                for position, case in enumerate(MURDER_MYSTERIES, start=1)
            ],
        )
        return
    if count != MURDER_MYSTERY_CASE_COUNT:
        raise ValueError(
            f"Round 6 must have exactly {MURDER_MYSTERY_CASE_COUNT} configured cases."
        )
    rows = connection.execute(
        """
        SELECT position, sudoku_size, sudoku_puzzle, sudoku_solution
        FROM murder_mystery_content ORDER BY position
        """
    ).fetchall()
    if [row[0] for row in rows] != list(range(1, MURDER_MYSTERY_CASE_COUNT + 1)):
        raise ValueError("Round 6 case positions must be numbered 1 through 15.")
    if any(
        row[1] != SUDOKU_SIZE
        or len(row[2]) != SUDOKU_SIZE**2
        or len(row[3]) != SUDOKU_SIZE**2
        for row in rows
    ):
        for position in range(1, MURDER_MYSTERY_CASE_COUNT + 1):
            default_puzzle = DEFAULT_SUDOKU_PUZZLES[position - 1]
            connection.execute(
                """
                UPDATE murder_mystery_content SET sudoku_puzzle = ?,
                    sudoku_solution = ?, sudoku_size = ?
                WHERE position = ?
                """,
                (
                    default_puzzle["puzzle"],
                    default_puzzle["solution"],
                    SUDOKU_SIZE,
                    position,
                ),
            )
        connection.execute("DELETE FROM murder_mystery_progress")
        connection.execute("DELETE FROM murder_mystery_assignments")
    elif any(not row[2] or not row[3] for row in rows):
        raise ValueError("Every Round 6 case needs a configured Sudoku.")


def load_murder_mystery_content() -> list[dict[str, Any]]:
    with closing(sqlite3.connect(ASSIGNMENT_DB_PATH, timeout=30)) as connection, connection:
        create_database_tables(connection)
        ensure_murder_mystery_content(connection)
        rows = connection.execute(
            """
            SELECT position, title, briefing, characters_json, evidence_json,
                culprit, sudoku_puzzle, sudoku_solution
            FROM murder_mystery_content ORDER BY position
            """
        ).fetchall()
    return [
        {
            "position": row[0],
            "title": row[1],
            "briefing": row[2],
            "characters": json.loads(row[3]),
            "evidence": json.loads(row[4]),
            "culprit": row[5],
            "sudoku_puzzle": "\n".join(
                row[6][index : index + SUDOKU_SIZE]
                for index in range(0, SUDOKU_SIZE**2, SUDOKU_SIZE)
            ),
            "sudoku_solution": "\n".join(
                row[7][index : index + SUDOKU_SIZE]
                for index in range(0, SUDOKU_SIZE**2, SUDOKU_SIZE)
            ),
        }
        for row in rows
    ]


def save_murder_mystery_content(cases: list[dict[str, Any]]) -> None:
    if len(cases) != MURDER_MYSTERY_CASE_COUNT:
        raise ValueError(
            f"Round 6 requires exactly {MURDER_MYSTERY_CASE_COUNT} mystery sets."
        )

    normalized_cases: list[
        tuple[int, str, str, str, str, str, str, str, str, str, int]
    ] = []
    for position, case in enumerate(cases, start=1):
        if not isinstance(case, dict):
            raise ValueError(f"Case {position} must be a case object.")
        required_text = (
            ("title", "title", 200),
            ("briefing", "briefing", 2000),
            ("culprit", "culprit", 100),
        )
        values: dict[str, str] = {}
        for field, label, max_length in required_text:
            value = case.get(field)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"Case {position} needs a {label}.")
            value = value.strip()
            if len(value) > max_length:
                raise ValueError(
                    f"Each case {label} must be {max_length} characters or fewer."
                )
            values[field] = value

        characters = case.get("characters")
        if not isinstance(characters, list) or len(characters) < 8:
            raise ValueError(f"Case {position} needs at least eight characters.")
        normalized_characters: list[dict[str, str]] = []
        character_names: set[str] = set()
        for character in characters:
            if not isinstance(character, dict):
                raise ValueError(
                    f"Each character in case {position} needs a name and role."
                )
            name = character.get("name")
            role = character.get("role")
            if (
                not isinstance(name, str)
                or not name.strip()
                or not isinstance(role, str)
                or not role.strip()
            ):
                raise ValueError(
                    f"Each character in case {position} needs a name and role."
                )
            name, role = name.strip(), role.strip()
            if len(name) > 100 or len(role) > 200:
                raise ValueError(
                    f"Character names and roles in case {position} are too long."
                )
            normalized_name = name.casefold()
            if normalized_name in character_names:
                raise ValueError(
                    f"Character names in case {position} must be unique."
                )
            character_names.add(normalized_name)
            normalized_characters.append({"name": name, "role": role})
        if values["culprit"].casefold() not in character_names:
            raise ValueError(
                f"The culprit for case {position} must be one of its characters."
            )

        evidence = case.get("evidence")
        if not isinstance(evidence, list) or not evidence:
            raise ValueError(f"Case {position} needs at least one evidence clue.")
        normalized_evidence = []
        for clue in evidence:
            if not isinstance(clue, str) or not clue.strip():
                raise ValueError(f"Evidence clues in case {position} cannot be blank.")
            clue = clue.strip()
            if len(clue) > 1000:
                raise ValueError(
                    f"Evidence clues in case {position} must be 1000 characters or fewer."
                )
            normalized_evidence.append(clue)

        sudoku_puzzle, sudoku_solution = validate_sudoku_case(
            case.get("sudoku_puzzle"), case.get("sudoku_solution")
        )
        normalized_cases.append(
            (
                position,
                values["title"],
                values["briefing"],
                json.dumps(normalized_characters, ensure_ascii=False),
                json.dumps(normalized_evidence, ensure_ascii=False),
                values["culprit"],
                "",
                "",
                sudoku_puzzle,
                sudoku_solution,
                SUDOKU_SIZE,
            )
        )

    with closing(sqlite3.connect(ASSIGNMENT_DB_PATH, timeout=30)) as connection, connection:
        connection.execute("BEGIN IMMEDIATE")
        create_database_tables(connection)
        connection.execute("DELETE FROM murder_mystery_progress")
        connection.execute("DELETE FROM murder_mystery_assignments")
        connection.execute("DELETE FROM murder_mystery_content")
        connection.executemany(
            """
            INSERT INTO murder_mystery_content (
                position, title, briefing, characters_json, evidence_json,
                culprit, word_puzzle, word_answer, sudoku_puzzle,
                sudoku_solution, sudoku_size
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            normalized_cases,
        )


def ensure_murder_mystery_assignment(
    connection: sqlite3.Connection, team: str
) -> int:
    ensure_murder_mystery_content(connection)
    assignment = connection.execute(
        "SELECT case_position FROM murder_mystery_assignments WHERE team = ?",
        (team,),
    ).fetchone()
    if assignment is not None:
        return assignment[0]

    assigned_positions = {
        row[0]
        for row in connection.execute(
            "SELECT case_position FROM murder_mystery_assignments"
        )
    }
    available_positions = [
        row[0]
        for row in connection.execute(
            "SELECT position FROM murder_mystery_content ORDER BY position"
        )
        if row[0] not in assigned_positions
    ]
    if not available_positions:
        raise ValueError("No unassigned Round 6 murder mysteries remain.")
    case_position = random.choice(available_positions)
    connection.execute(
        """
        INSERT INTO murder_mystery_assignments (team, case_position)
        VALUES (?, ?)
        """,
        (team, case_position),
    )
    return case_position


def get_murder_mystery_state(team: str) -> dict[str, Any]:
    with closing(sqlite3.connect(ASSIGNMENT_DB_PATH, timeout=30)) as connection, connection:
        connection.execute("BEGIN IMMEDIATE")
        create_database_tables(connection)
        case_position = ensure_murder_mystery_assignment(connection, team)
        connection.execute(
            "INSERT OR IGNORE INTO murder_mystery_progress (team) VALUES (?)",
            (team,),
        )
        progress = connection.execute(
            """
            SELECT mystery_attempts, sudoku_attempts, mystery_solved, sudoku_solved,
                penalty_seconds
            FROM murder_mystery_progress WHERE team = ?
            """,
            (team,),
        ).fetchone()
        case_row = connection.execute(
            """
            SELECT title, briefing, characters_json, evidence_json, sudoku_puzzle
            FROM murder_mystery_content WHERE position = ?
            """,
            (case_position,),
        ).fetchone()
    if case_row is None:
        raise ValueError("The assigned Round 6 case is not configured.")
    return {
        "case_position": case_position,
        "case": {
            "title": case_row[0],
            "briefing": case_row[1],
            "characters": json.loads(case_row[2]),
            "evidence": json.loads(case_row[3]),
            "sudoku_puzzle": case_row[4],
        },
        "mystery_attempts": progress[0],
        "sudoku_attempts": progress[1],
        "mystery_solved": bool(progress[2]),
        "sudoku_solved": bool(progress[3]),
        "penalty_seconds": progress[4],
    }


def submit_murder_mystery_answer(
    team: str, challenge: str, answer: str
) -> dict[str, Any]:
    if challenge not in ("mystery", "sudoku"):
        raise ValueError("Choose either the murder mystery or Sudoku.")
    if not isinstance(answer, str) or not answer.strip():
        raise ValueError("Enter an answer.")
    if challenge == "sudoku" and not re.fullmatch(
        rf"[1-{SUDOKU_SIZE}]{{{SUDOKU_SIZE ** 2}}}", answer
    ):
        raise ValueError("Fill every 4x4 Sudoku cell before submitting.")
    penalty_settings = load_config()["round_penalties"]["6"]

    with closing(sqlite3.connect(ASSIGNMENT_DB_PATH, timeout=30)) as connection, connection:
        connection.execute("BEGIN IMMEDIATE")
        create_database_tables(connection)
        case_position = ensure_murder_mystery_assignment(connection, team)
        connection.execute(
            "INSERT OR IGNORE INTO murder_mystery_progress (team) VALUES (?)",
            (team,),
        )
        answer_column = (
            "mystery_attempts" if challenge == "mystery" else "sudoku_attempts"
        )
        solved_column = (
            "mystery_solved" if challenge == "mystery" else "sudoku_solved"
        )
        progress = connection.execute(
            f"""
            SELECT {answer_column}, {solved_column}, penalty_seconds
            FROM murder_mystery_progress WHERE team = ?
            """,
            (team,),
        ).fetchone()
        attempts, solved, penalty_seconds = progress
        if solved:
            raise ValueError("This puzzle has already been solved.")

        answer_field = "culprit" if challenge == "mystery" else "sudoku_solution"
        expected_answer = connection.execute(
            f"SELECT {answer_field} FROM murder_mystery_content WHERE position = ?",
            (case_position,),
        ).fetchone()
        if expected_answer is None:
            raise ValueError("The assigned Round 6 case is not configured.")
        expected_answer = expected_answer[0]
        normalize = lambda value: " ".join(value.split()).casefold().encode("utf-8")
        is_correct = hmac.compare_digest(
            normalize(answer), normalize(expected_answer)
        )
        added_penalty = 0
        if is_correct:
            connection.execute(
                f"UPDATE murder_mystery_progress SET {solved_column} = 1 WHERE team = ?",
                (team,),
            )
        else:
            attempts += 1
            if attempts > penalty_settings["free_attempts"]:
                added_penalty = penalty_settings["penalty_seconds"]
                penalty_seconds += added_penalty
            connection.execute(
                f"""
                UPDATE murder_mystery_progress
                SET {answer_column} = ?, penalty_seconds = ?
                WHERE team = ?
                """,
                (attempts, penalty_seconds, team),
            )

    return {
        "is_correct": is_correct,
        "attempts": attempts,
        "added_penalty": added_penalty,
        "penalty_seconds": penalty_seconds,
    }


def ensure_visual_puzzle_assignment(
    connection: sqlite3.Connection, team: str
) -> list[int]:
    available_positions = [
        row[0]
        for row in connection.execute(
            "SELECT position FROM visual_puzzles ORDER BY position"
        )
    ]
    if len(available_positions) < VISUAL_PUZZLE_COUNT:
        raise ValueError(
            "Round 4 needs at least four visual puzzles. Please ask an admin to configure it."
        )
    assigned_positions = [
        row[0]
        for row in connection.execute(
            """
            SELECT puzzle_position FROM visual_puzzle_assignments
            WHERE team = ? ORDER BY sequence_position
            """,
            (team,),
        )
    ]
    if len(assigned_positions) != VISUAL_PUZZLE_COUNT:
        selected_positions = random.sample(
            available_positions, VISUAL_PUZZLE_COUNT
        )
        connection.execute(
            "DELETE FROM visual_puzzle_assignments WHERE team = ?",
            (team,),
        )
        connection.executemany(
            """
            INSERT INTO visual_puzzle_assignments
                (team, sequence_position, puzzle_position)
            VALUES (?, ?, ?)
            """,
            [
                (team, sequence_position, puzzle_position)
                for sequence_position, puzzle_position in enumerate(
                    selected_positions, start=1
                )
            ],
        )
        connection.execute(
            """
            UPDATE visual_puzzle_progress
            SET current_position = 1, wrong_attempts = 0, completed = 0
            WHERE team = ?
            """,
            (team,),
        )
        assigned_positions = selected_positions
    return assigned_positions


def get_visual_puzzle_progress(team: str) -> dict[str, Any]:
    with closing(sqlite3.connect(ASSIGNMENT_DB_PATH, timeout=30)) as connection, connection:
        connection.execute("BEGIN IMMEDIATE")
        create_database_tables(connection)
        connection.execute(
            "INSERT OR IGNORE INTO visual_puzzle_progress (team) VALUES (?)",
            (team,),
        )
        assigned_positions = ensure_visual_puzzle_assignment(connection, team)
        row = connection.execute(
            """
            SELECT current_position, wrong_attempts, penalty_seconds, completed
            FROM visual_puzzle_progress WHERE team = ?
            """,
            (team,),
        ).fetchone()
    return {
        "current_position": row[0],
        "wrong_attempts": row[1],
        "penalty_seconds": row[2],
        "completed": bool(row[3]),
        "puzzle_positions": assigned_positions,
    }


def submit_visual_puzzle_answer(team: str, answer: str) -> dict[str, Any]:
    if not isinstance(answer, str) or not answer.strip():
        raise ValueError("Enter an answer.")
    penalty_settings = load_config()["round_penalties"]["4"]

    with closing(sqlite3.connect(ASSIGNMENT_DB_PATH, timeout=30)) as connection, connection:
        connection.execute("BEGIN IMMEDIATE")
        create_database_tables(connection)
        connection.execute(
            "INSERT OR IGNORE INTO visual_puzzle_progress (team) VALUES (?)",
            (team,),
        )
        ensure_visual_puzzle_assignment(connection, team)
        progress = connection.execute(
            """
            SELECT current_position, wrong_attempts, penalty_seconds, completed
            FROM visual_puzzle_progress WHERE team = ?
            """,
            (team,),
        ).fetchone()
        position, wrong_attempts, penalty_seconds, completed = progress
        if completed or position > VISUAL_PUZZLE_COUNT:
            raise ValueError("All visual puzzles have already been solved.")
        puzzle = connection.execute(
            """
            SELECT puzzles.answer, puzzles.code_segment
            FROM visual_puzzle_assignments assignments
            JOIN visual_puzzles puzzles
                ON puzzles.position = assignments.puzzle_position
            WHERE assignments.team = ? AND assignments.sequence_position = ?
            """,
            (team, position),
        ).fetchone()
        if puzzle is None:
            raise ValueError("Round 4 is not ready yet. Please ask an admin to configure it.")

        normalize = lambda value: " ".join(value.split()).casefold().encode("utf-8")
        is_correct = hmac.compare_digest(normalize(answer), normalize(puzzle[0]))
        if is_correct:
            connection.execute(
                """
                UPDATE visual_puzzle_progress
                SET current_position = ?, wrong_attempts = 0
                WHERE team = ?
                """,
                (position + 1, team),
            )
            wrong_attempts = 0
            segment = puzzle[1]
        else:
            wrong_attempts += 1
            added_penalty = (
                penalty_settings["penalty_seconds"]
                if wrong_attempts > penalty_settings["free_attempts"]
                else 0
            )
            penalty_seconds += added_penalty
            connection.execute(
                """
                UPDATE visual_puzzle_progress
                SET wrong_attempts = ?, penalty_seconds = ?
                WHERE team = ?
                """,
                (wrong_attempts, penalty_seconds, team),
            )
            segment = None

    return {
        "is_correct": is_correct,
        "segment": segment,
        "wrong_attempts": wrong_attempts,
        "penalty_seconds": penalty_seconds,
    }


def submit_visual_puzzle_final_code(team: str, code: str) -> bool:
    if not isinstance(code, str) or not re.fullmatch(r"[0-9]{64}", code.strip()):
        raise ValueError("Enter the complete 64-digit code.")

    with closing(sqlite3.connect(ASSIGNMENT_DB_PATH, timeout=30)) as connection, connection:
        connection.execute("BEGIN IMMEDIATE")
        create_database_tables(connection)
        connection.execute(
            "INSERT OR IGNORE INTO visual_puzzle_progress (team) VALUES (?)",
            (team,),
        )
        ensure_visual_puzzle_assignment(connection, team)
        progress = connection.execute(
            """
            SELECT current_position, completed FROM visual_puzzle_progress
            WHERE team = ?
            """,
            (team,),
        ).fetchone()
        current_position, completed = progress
        if completed:
            return True
        if current_position <= VISUAL_PUZZLE_COUNT:
            raise ValueError("Solve all four visual puzzles before entering the code.")
        rows = connection.execute(
            """
            SELECT puzzles.code_segment
            FROM visual_puzzle_assignments assignments
            JOIN visual_puzzles puzzles
                ON puzzles.position = assignments.puzzle_position
            WHERE assignments.team = ?
            ORDER BY assignments.sequence_position
            """,
            (team,),
        ).fetchall()
        if len(rows) != VISUAL_PUZZLE_COUNT:
            raise ValueError("Round 4 is not ready yet. Please ask an admin to configure it.")
        expected_code = "".join(row[0] for row in rows)
        is_correct = hmac.compare_digest(code.strip().encode("ascii"), expected_code.encode("ascii"))
        if is_correct:
            connection.execute(
                "UPDATE visual_puzzle_progress SET completed = 1 WHERE team = ?",
                (team,),
            )
    return is_correct


def get_team_penalty_seconds(team: str) -> int:
    with closing(sqlite3.connect(ASSIGNMENT_DB_PATH, timeout=30)) as connection, connection:
        create_database_tables(connection)
        hotel_detective_penalty = connection.execute(
            """
            SELECT COALESCE(SUM(penalty_seconds), 0)
            FROM hotel_detective_progress WHERE team = ?
            """,
            (team,),
        ).fetchone()[0]
        row = connection.execute(
            "SELECT penalty_seconds FROM visual_puzzle_progress WHERE team = ?",
            (team,),
        ).fetchone()
        hidden_photo_penalty = connection.execute(
            """
            SELECT COALESCE(SUM(penalty_seconds), 0)
            FROM hidden_photograph_submissions WHERE team = ?
            """,
            (team,),
        ).fetchone()[0]
        murder_mystery_penalty = connection.execute(
            """
            SELECT penalty_seconds FROM murder_mystery_progress WHERE team = ?
            """,
            (team,),
        ).fetchone()
    return (
        hotel_detective_penalty
        + (row[0] if row is not None else 0)
        + hidden_photo_penalty
        + (murder_mystery_penalty[0] if murder_mystery_penalty is not None else 0)
    )


def get_team_timer(team: str) -> Optional[tuple[float, Optional[float]]]:
    with closing(sqlite3.connect(ASSIGNMENT_DB_PATH, timeout=30)) as connection, connection:
        create_database_tables(connection)
        timer = connection.execute(
            "SELECT started_at, finished_at FROM team_timers WHERE team = ?",
            (team,),
        ).fetchone()
    return timer


def get_team_reset_revision(team: str) -> int:
    with closing(sqlite3.connect(ASSIGNMENT_DB_PATH, timeout=30)) as connection, connection:
        create_database_tables(connection)
        row = connection.execute(
            "SELECT revision FROM team_reset_revisions WHERE team = ?", (team,)
        ).fetchone()
    return row[0] if row is not None else 0


def reset_team_games(teams: list[str], from_round: int = 1) -> None:
    if not teams or any(not team for team in teams) or from_round not in ROUND_NUMBERS:
        raise ValueError("Select valid teams and a round to reset.")
    with closing(sqlite3.connect(ASSIGNMENT_DB_PATH, timeout=30)) as connection, connection:
        connection.execute("BEGIN IMMEDIATE")
        create_database_tables(connection)
        for team in set(teams):
            connection.execute("DELETE FROM team_timers WHERE team = ?", (team,))
            connection.execute(
                "DELETE FROM team_round_timers WHERE team = ? AND round_number >= ?",
                (team, from_round),
            )
            connection.execute(
                "DELETE FROM team_round_unlock_codes WHERE team = ? AND round_number > ?",
                (team, from_round),
            )
            if from_round <= 4:
                connection.execute(
                    """
                    UPDATE visual_puzzle_progress
                    SET current_position = 1, wrong_attempts = 0,
                        penalty_seconds = 0, completed = 0
                    WHERE team = ?
                    """,
                    (team,),
                )
            if from_round <= 2:
                connection.execute(
                    "DELETE FROM hotel_detective_progress WHERE team = ?",
                    (team,),
                )
            if from_round <= 5:
                connection.execute(
                    "DELETE FROM hidden_photograph_submissions WHERE team = ?", (team,)
                )
            if from_round <= 6:
                connection.execute(
                    "DELETE FROM murder_mystery_progress WHERE team = ?", (team,)
                )
            connection.execute(
                """
                INSERT INTO team_reset_revisions (team, revision) VALUES (?, 1)
                ON CONFLICT(team) DO UPDATE SET revision = revision + 1
                """,
                (team,),
            )


def get_unlocked_rounds(team: str, rounds: list[int]) -> list[int]:
    with closing(sqlite3.connect(ASSIGNMENT_DB_PATH, timeout=30)) as connection, connection:
        create_database_tables(connection)
        finished = {
            row[0]
            for row in connection.execute(
                """
                SELECT round_number FROM team_round_timers
                WHERE team = ? AND finished_at IS NOT NULL
                """,
                (team,),
            )
        }
    unlocked = []
    for round_number in sorted(rounds):
        unlocked.append(round_number)
        if round_number not in finished:
            break
    return unlocked


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


def start_team_round_timer(
    team: str, round_number: int
) -> tuple[float, Optional[float]]:
    with closing(sqlite3.connect(ASSIGNMENT_DB_PATH, timeout=30)) as connection, connection:
        connection.execute("BEGIN IMMEDIATE")
        create_database_tables(connection)
        connection.execute(
            """
            INSERT OR IGNORE INTO team_round_timers (team, round_number, started_at)
            VALUES (?, ?, ?)
            """,
            (team, round_number, time.time()),
        )
        timer = connection.execute(
            """
            SELECT started_at, finished_at FROM team_round_timers
            WHERE team = ? AND round_number = ?
            """,
            (team, round_number),
        ).fetchone()
    return timer


def finish_team_round_timer(
    team: str, round_number: int, unlock_code: Optional[str] = None
) -> tuple[float, Optional[float]]:
    with closing(sqlite3.connect(ASSIGNMENT_DB_PATH, timeout=30)) as connection, connection:
        connection.execute("BEGIN IMMEDIATE")
        create_database_tables(connection)
        connection.execute(
            """
            UPDATE team_round_timers
            SET finished_at = ?
            WHERE team = ? AND round_number = ? AND finished_at IS NULL
            """,
            (time.time(), team, round_number),
        )
        timer = connection.execute(
            """
            SELECT started_at, finished_at FROM team_round_timers
            WHERE team = ? AND round_number = ?
            """,
            (team, round_number),
        ).fetchone()
        if timer is not None:
            later_rounds = [
                number for number in ROUND_NUMBERS if number > round_number
            ]
            if later_rounds:
                next_round = min(later_rounds)
                code = unlock_code.strip() if isinstance(unlock_code, str) else ""
                if code:
                    connection.execute(
                        """
                        INSERT INTO team_round_unlock_codes
                            (team, round_number, code)
                        VALUES (?, ?, ?)
                        ON CONFLICT(team, round_number) DO UPDATE SET
                            entered_at = CASE
                                WHEN team_round_unlock_codes.code != excluded.code
                                THEN NULL
                                ELSE team_round_unlock_codes.entered_at
                            END,
                            code = excluded.code
                        """,
                        (team, next_round, code),
                    )
                elif round_number != 1:
                    connection.execute(
                        """
                        INSERT OR IGNORE INTO team_round_unlock_codes
                            (team, round_number, code)
                        VALUES (?, ?, ?)
                        """,
                        (team, next_round, secrets.token_hex(3).upper()),
                    )
    if timer is None:
        raise ValueError("The team's round timer has not been started.")
    return timer


def get_round_unlock_code(team: str, round_number: int) -> Optional[str]:
    if round_number not in ROUND_NUMBERS:
        raise ValueError("Select a valid round.")
    previous_rounds = [number for number in ROUND_NUMBERS if number < round_number]
    if not previous_rounds:
        return None

    previous_round = max(previous_rounds)
    with closing(sqlite3.connect(ASSIGNMENT_DB_PATH, timeout=30)) as connection, connection:
        connection.execute("BEGIN IMMEDIATE")
        create_database_tables(connection)
        completed = connection.execute(
            """
            SELECT 1 FROM team_round_timers
            WHERE team = ? AND round_number = ? AND finished_at IS NOT NULL
            """,
            (team, previous_round),
        ).fetchone()
        if completed is None:
            return None
        if round_number == 2:
            assignment = connection.execute(
                """
                SELECT place_index FROM assignments
                WHERE team = ? AND round_number = 1
                """,
                (team,),
            ).fetchone()
            if assignment is not None:
                configured_code = load_config()["places"][assignment[0]].get(
                    "entry_code", ""
                )
                configured_code = (
                    configured_code.strip()
                    if isinstance(configured_code, str)
                    else ""
                )
                if not configured_code:
                    connection.execute(
                        """
                        DELETE FROM team_round_unlock_codes
                        WHERE team = ? AND round_number = ?
                        """,
                        (team, round_number),
                    )
                    return None
                connection.execute(
                    """
                    INSERT INTO team_round_unlock_codes
                        (team, round_number, code)
                    VALUES (?, ?, ?)
                    ON CONFLICT(team, round_number) DO UPDATE SET
                        entered_at = CASE
                            WHEN team_round_unlock_codes.code != excluded.code
                            THEN NULL
                            ELSE team_round_unlock_codes.entered_at
                        END,
                        code = excluded.code
                    """,
                    (team, round_number, configured_code),
                )
            else:
                existing = connection.execute(
                    """
                    SELECT code FROM team_round_unlock_codes
                    WHERE team = ? AND round_number = ?
                    """,
                    (team, round_number),
                ).fetchone()
                if existing is None:
                    return None
        else:
            connection.execute(
                """
                INSERT OR IGNORE INTO team_round_unlock_codes
                    (team, round_number, code)
                VALUES (?, ?, ?)
                """,
                (team, round_number, secrets.token_hex(3).upper()),
            )
        row = connection.execute(
            """
            SELECT code FROM team_round_unlock_codes
            WHERE team = ? AND round_number = ?
            """,
            (team, round_number),
        ).fetchone()
    return row[0] if row is not None else None


def round_unlock_was_entered(team: str, round_number: int) -> bool:
    with closing(sqlite3.connect(ASSIGNMENT_DB_PATH, timeout=30)) as connection, connection:
        create_database_tables(connection)
        row = connection.execute(
            """
            SELECT entered_at FROM team_round_unlock_codes
            WHERE team = ? AND round_number = ?
            """,
            (team, round_number),
        ).fetchone()
    return row is not None and row[0] is not None


def submit_round_unlock_code(team: str, round_number: int, code: str) -> bool:
    expected_code = get_round_unlock_code(team, round_number)
    if expected_code is None or not isinstance(code, str):
        return False
    if not hmac.compare_digest(
        code.strip().encode("utf-8"), expected_code.encode("utf-8")
    ):
        return False
    with closing(sqlite3.connect(ASSIGNMENT_DB_PATH, timeout=30)) as connection, connection:
        connection.execute(
            """
            UPDATE team_round_unlock_codes
            SET entered_at = COALESCE(entered_at, ?)
            WHERE team = ? AND round_number = ? AND code = ?
            """,
            (time.time(), team, round_number, expected_code),
        )
        return connection.total_changes > 0


def set_timer_freeze(
    frozen: bool, team: Optional[str] = None, round_number: Optional[int] = None
) -> None:
    if (team is None) != (round_number is None):
        raise ValueError("Specify both a team and a round, or neither.")
    if team is not None and (not team or round_number not in ROUND_NUMBERS):
        raise ValueError("A valid team and round are required.")
    with closing(sqlite3.connect(ASSIGNMENT_DB_PATH, timeout=30)) as connection, connection:
        connection.execute("BEGIN IMMEDIATE")
        create_database_tables(connection)
        active = connection.execute(
            """
            SELECT 1 FROM timer_freezes
            WHERE team IS ? AND round_number IS ? AND finished_at IS NULL
            """,
            (team, round_number),
        ).fetchone()
        if frozen and active is None:
            connection.execute(
                "INSERT INTO timer_freezes (team, round_number, started_at) VALUES (?, ?, ?)",
                (team, round_number, time.time()),
            )
        elif not frozen and active is not None:
            connection.execute(
                """
                UPDATE timer_freezes SET finished_at = ?
                WHERE team IS ? AND round_number IS ? AND finished_at IS NULL
                """,
                (time.time(), team, round_number),
            )


def get_active_timer_freezes() -> tuple[bool, set[tuple[str, int]]]:
    with closing(sqlite3.connect(ASSIGNMENT_DB_PATH, timeout=30)) as connection, connection:
        create_database_tables(connection)
        freezes = connection.execute(
            "SELECT team, round_number FROM timer_freezes WHERE finished_at IS NULL"
        ).fetchall()
    return (
        any(team is None for team, _ in freezes),
        {(team, number) for team, number in freezes if team is not None},
    )


def get_admin_set_assignments(teams: list[str]) -> list[dict[str, str]]:
    with closing(sqlite3.connect(ASSIGNMENT_DB_PATH, timeout=30)) as connection, connection:
        create_database_tables(connection)
        hotel_assignments = dict(
            connection.execute(
                "SELECT team, set_number FROM hotel_detective_assignments"
            )
        )
        whitepaper_assignments = dict(
            connection.execute(
                "SELECT team, set_number FROM whitepaper_assignments"
            )
        )

    return [
        {
            "Team": team,
            "Round 2: Statement set": (
                f"Set {hotel_assignments[team]}"
                if team in hotel_assignments
                else "Not assigned"
            ),
            "Round 3: Question set": (
                f"Set {whitepaper_assignments[team]}"
                if team in whitepaper_assignments
                else "Not assigned"
            ),
        }
        for team in teams
    ]


def elapsed_without_freezes(
    started_at: float, ended_at: float, freezes: list[tuple[float, float]]
) -> float:
    intervals = sorted(
        (max(started_at, start), min(ended_at, end))
        for start, end in freezes
        if start < end and start < ended_at and end > started_at
    )
    frozen_seconds = 0.0
    last_end = started_at
    for start, end in intervals:
        frozen_seconds += max(0, end - max(start, last_end))
        last_end = max(last_end, end)
    return max(0, ended_at - started_at - frozen_seconds)


def get_admin_team_timing(
    teams: list[str], rounds: list[int]
) -> list[dict[str, Any]]:
    with closing(sqlite3.connect(ASSIGNMENT_DB_PATH, timeout=30)) as connection, connection:
        create_database_tables(connection)
        team_timers = {
            row[0]: (row[1], row[2])
            for row in connection.execute(
                "SELECT team, started_at, finished_at FROM team_timers"
            )
        }
        round_timers = {
            (row[0], row[1]): (row[2], row[3])
            for row in connection.execute(
                """
                SELECT team, round_number, started_at, finished_at
                FROM team_round_timers
                """
            )
        }
        freezes = connection.execute(
            "SELECT team, round_number, started_at, finished_at FROM timer_freezes"
        ).fetchall()
        penalties = {
            row[0]: row[1]
            for row in connection.execute(
                "SELECT team, penalty_seconds FROM visual_puzzle_progress"
            )
        }
        for penalty_rows in (
            connection.execute(
                """
                SELECT team, COALESCE(SUM(penalty_seconds), 0)
                FROM hotel_detective_progress GROUP BY team
                """
            ),
            connection.execute(
                """
                SELECT team, COALESCE(SUM(penalty_seconds), 0)
                FROM hidden_photograph_submissions GROUP BY team
                """
            ),
            connection.execute(
                """
                SELECT team, penalty_seconds FROM murder_mystery_progress
                """
            ),
        ):
            for team, penalty_seconds in penalty_rows:
                penalties[team] = penalties.get(team, 0) + penalty_seconds

    now = time.time()
    master_freezes = [
        (start, end if end is not None else now)
        for team, _, start, end in freezes if team is None
    ]
    round_freezes: dict[tuple[str, int], list[tuple[float, float]]] = {}
    for team, number, start, end in freezes:
        if team is not None:
            round_freezes.setdefault((team, number), []).append(
                (start, end if end is not None else now)
            )
    summaries = []
    for team in teams:
        timer = team_timers.get(team)
        finished_at = timer[1] if timer is not None else None
        team_freezes = list(master_freezes)
        for (freeze_team, number), intervals in round_freezes.items():
            round_timer = round_timers.get((freeze_team, number))
            if freeze_team == team and round_timer is not None:
                round_end = round_timer[1] if round_timer[1] is not None else now
                team_freezes.extend(
                    (max(start, round_timer[0]), min(end, round_end))
                    for start, end in intervals
                )
        elapsed_seconds = (
            elapsed_without_freezes(
                timer[0], finished_at if finished_at is not None else now, team_freezes
            ) if timer is not None else None
        )
        penalty_seconds = penalties.get(team, 0)
        round_summaries = []
        for round_number in rounds:
            round_timer = round_timers.get((team, round_number))
            if round_timer is None:
                round_elapsed_seconds = None
                round_completed = False
            else:
                round_finished_at = round_timer[1]
                round_end = round_finished_at
                if round_end is None:
                    round_end = min(now, finished_at) if finished_at is not None else now
                round_elapsed_seconds = elapsed_without_freezes(
                    round_timer[0],
                    round_end,
                    master_freezes + round_freezes.get((team, round_number), []),
                )
                round_completed = round_finished_at is not None
            round_summaries.append(
                {
                    "round_number": round_number,
                    "elapsed_seconds": round_elapsed_seconds,
                    "completed": round_completed,
                }
            )
        summaries.append(
            {
                "team": team,
                "elapsed_seconds": elapsed_seconds,
                "penalty_seconds": penalty_seconds,
                "adjusted_seconds": (
                    elapsed_seconds + penalty_seconds
                    if elapsed_seconds is not None
                    else None
                ),
                "finished": finished_at is not None,
                "rounds": round_summaries,
            }
        )
    return summaries


def format_elapsed_time(seconds: float) -> str:
    total_seconds = max(0, int(seconds))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02}:{minutes:02}:{seconds:02}"


def render_team_timer(st: Any, team: str) -> None:
    @st.fragment(run_every="1s")
    def timer_display() -> None:
        if (
            "team_reset_revision" in st.session_state
            and st.session_state.team_reset_revision != get_team_reset_revision(team)
        ):
            st.rerun()
        summary = get_admin_team_timing([team], ROUND_NUMBERS)[0]
        elapsed = summary["adjusted_seconds"]
        if elapsed is None:
            return
        penalty_seconds = summary["penalty_seconds"]
        label = "Final time" if summary["finished"] else "Time elapsed"
        st.metric(label, format_elapsed_time(elapsed))
        master_frozen, frozen_rounds = get_active_timer_freezes()
        if not summary["finished"] and (
            master_frozen or any(
                (team, item["round_number"]) in frozen_rounds
                and item["elapsed_seconds"] is not None and not item["completed"]
                for item in summary["rounds"]
            )
        ):
            st.caption("Timer frozen by an admin.")
        if penalty_seconds:
            st.caption(
                f"Penalties added: {format_elapsed_time(penalty_seconds)}"
            )

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


def team_names_for_count(teams: list[str], team_count: int) -> list[str]:
    if team_count < 1:
        raise ValueError("Team count must be at least 1.")

    updated_teams = teams[:team_count]
    next_default_number = 1
    while len(updated_teams) < team_count:
        default_name = f"Team {next_default_number}"
        next_default_number += 1
        if default_name not in updated_teams:
            updated_teams.append(default_name)
    return updated_teams


def validate_config(config: dict[str, Any]) -> None:
    if not isinstance(config, dict):
        raise ValueError("Configuration must be a JSON object.")
    if not isinstance(config.get("places"), list) or not config["places"]:
        raise ValueError("Configuration must contain a non-empty places list.")
    if len(config["places"]) != len(RIDDLES):
        raise ValueError("There must be exactly 20 configured places.")
    for place in config["places"]:
        if (
            not isinstance(place, dict)
            or not isinstance(place.get("name"), str)
            or not place["name"].strip()
            or not isinstance(place.get("puzzle"), str)
            or not place["puzzle"].strip()
            or not isinstance(place.get("room_number"), str)
            or not place["room_number"].strip()
            or not isinstance(place.get("entry_code", ""), str)
            or len(place.get("entry_code", "")) > 200
            or not isinstance(place.get("locations"), list)
            or not place["locations"]
        ):
            raise ValueError(
                "Each place needs a name, puzzle, room number, and at least one location."
            )
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
    round_penalties = config.get("round_penalties", ROUND_PENALTY_DEFAULTS)
    if not isinstance(round_penalties, dict):
        raise ValueError("round_penalties must be an object.")
    for round_number, defaults in ROUND_PENALTY_DEFAULTS.items():
        if "round_penalties" in config and round_number not in round_penalties:
            raise ValueError(f"Round {round_number} penalty settings are required.")
        settings = round_penalties.get(round_number, defaults)
        if not isinstance(settings, dict):
            raise ValueError(f"Round {round_number} penalty settings must be an object.")
        for setting in defaults:
            if setting not in settings:
                raise ValueError(f"Round {round_number} {setting} is required.")
            value = settings.get(setting)
            maximum = 100 if setting == "free_attempts" else 3600
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or value < 0
                or value > maximum
            ):
                raise ValueError(
                    f"Round {round_number} {setting} must be an integer "
                    f"between 0 and {maximum}."
                )
    if len(teams) > MURDER_MYSTERY_CASE_COUNT:
        raise ValueError(
            "Round 6 supports at most 15 teams because each team gets a unique mystery."
        )
    game_instructions = config.get("game_instructions", GAME_INSTRUCTION_DEFAULTS)
    if not isinstance(game_instructions, dict):
        raise ValueError("game_instructions must be an object.")
    for instruction_type in ("dos", "donts"):
        instructions = game_instructions.get(instruction_type)
        if not isinstance(instructions, list) or any(
            not isinstance(instruction, str)
            or not instruction.strip()
            or len(instruction) > 500
            for instruction in instructions
        ):
            raise ValueError(
                f"game_instructions.{instruction_type} must be a list of "
                "non-empty strings of at most 500 characters."
            )
    revision = config.get("config_revision", 0)
    if not isinstance(revision, int) or isinstance(revision, bool) or revision < 0:
        raise ValueError("config_revision must be a non-negative integer.")


def load_config() -> dict[str, Any]:
    with CONFIG_PATH.open(encoding="utf-8") as config_file:
        config = json.load(config_file)
    if isinstance(config, dict):
        config.pop("rounds", None)
        configured_instructions = config.setdefault(
            "game_instructions", copy.deepcopy(GAME_INSTRUCTION_DEFAULTS)
        )
        if isinstance(configured_instructions, dict):
            for instruction_type, defaults in GAME_INSTRUCTION_DEFAULTS.items():
                configured_instructions.setdefault(
                    instruction_type, copy.deepcopy(defaults)
                )
        if isinstance(config.get("places"), list):
            for place in config["places"]:
                if isinstance(place, dict):
                    place.setdefault("entry_code", "")
        configured_penalties = config.setdefault("round_penalties", {})
        if isinstance(configured_penalties, dict):
            for round_number, defaults in ROUND_PENALTY_DEFAULTS.items():
                settings = configured_penalties.setdefault(
                    round_number, copy.deepcopy(defaults)
                )
                if isinstance(settings, dict):
                    for setting, default in defaults.items():
                        settings.setdefault(setting, default)
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
        create_database_tables(connection)
        connection.execute("DELETE FROM assignments")
        connection.execute("DELETE FROM whitepaper_assignments")
        connection.execute("DELETE FROM murder_mystery_progress")
        connection.execute("DELETE FROM murder_mystery_assignments")


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


def editor_records(rows: Any) -> list[dict[str, Any]]:
    if hasattr(rows, "to_dict"):
        return rows.to_dict(orient="records")
    return [dict(row) for row in rows]


def room_group_ranges(place_count: int) -> list[tuple[int, int]]:
    ranges = [(1, min(4, place_count))]
    ranges.extend(
        (start, min(start + 4, place_count))
        for start in range(5, place_count + 1, 5)
    )
    return ranges


def render_hotel_detective_admin(st: Any) -> None:
    st.subheader(round_title(2))
    st.caption(
        "Enter up to 20 statements in order. Each consecutive group of five is a "
        "set, and teams are randomly assigned a complete set."
    )
    try:
        statements, codes = load_hotel_detective_content()
    except sqlite3.Error as error:
        st.error(f"Could not load Hotel Detective content: {error}")
        return

    statement_rows = [
        {
            "Set": f"Set {(position - 1) // 5 + 1}",
            **row,
        }
        for position, row in enumerate(statements, start=1)
    ]
    code_rows = [
        {"Set": f"Set {set_number}", "Unlock code": code}
        for set_number, code in enumerate(codes, start=1)
    ]
    with st.form("admin_hotel_detective"):
        edited_statements = st.data_editor(
            statement_rows,
            num_rows="fixed",
            hide_index=True,
            disabled=["Set"],
            column_config={
                "Set": st.column_config.TextColumn("Set"),
                "Statement": st.column_config.TextColumn(
                    "Statement", max_chars=500
                ),
                "Correct answer": st.column_config.SelectboxColumn(
                    "Correct answer", options=["", "True", "False"]
                ),
            },
            key="admin_hotel_statements",
        )
        edited_codes = st.data_editor(
            code_rows,
            num_rows="fixed",
            hide_index=True,
            disabled=["Set"],
            column_config={
                "Set": st.column_config.TextColumn("Set"),
                "Unlock code": st.column_config.TextColumn(
                    "Unlock code", max_chars=100
                ),
            },
            key="admin_hotel_codes",
        )
        save_submitted = st.form_submit_button(
            "Save Hotel Detective", type="primary"
        )

    if save_submitted:
        try:
            save_hotel_detective_content(
                [
                    {
                        "Statement": row["Statement"],
                        "Correct answer": row["Correct answer"],
                    }
                    for row in editor_records(edited_statements)
                ],
                [row["Unlock code"] for row in editor_records(edited_codes)],
            )
        except (ValueError, TypeError, KeyError, sqlite3.Error) as error:
            st.error(f"Could not save Hotel Detective content: {error}")
        else:
            st.session_state.pop("admin_hotel_statements", None)
            st.session_state.pop("admin_hotel_codes", None)
            st.success("Hotel Detective content saved.")
            st.rerun()


def render_whitepaper_search_admin(st: Any) -> None:
    st.subheader(round_title(3))
    st.caption(
        "Configure four sets of four questions from IXO whitepapers. Teams must "
        "answer all four questions in a set correctly to reveal that set's code."
    )
    try:
        questions, codes = load_whitepaper_content()
    except sqlite3.Error as error:
        st.error(f"Could not load whitepaper questions: {error}")
        return

    question_rows = [
        {
            "Set": f"Set {(position - 1) // 4 + 1}",
            "Question #": position,
            **row,
        }
        for position, row in enumerate(questions, start=1)
    ]
    code_rows = [
        {"Set": f"Set {set_number}", "Unlock code": code}
        for set_number, code in enumerate(codes, start=1)
    ]
    with st.form("admin_whitepaper_questions"):
        edited_questions = st.data_editor(
            question_rows,
            num_rows="fixed",
            hide_index=True,
            disabled=["Set", "Question #"],
            column_config={
                "Set": st.column_config.TextColumn("Set"),
                "Question #": st.column_config.NumberColumn("Question #"),
                "Question": st.column_config.TextColumn("Question", max_chars=500),
                "Correct answer": st.column_config.TextColumn(
                    "Correct answer", max_chars=200
                ),
            },
            key="admin_whitepaper_questions",
        )
        edited_codes = st.data_editor(
            code_rows,
            num_rows="fixed",
            hide_index=True,
            disabled=["Set"],
            column_config={
                "Set": st.column_config.TextColumn("Set"),
                "Unlock code": st.column_config.TextColumn(
                    "Unlock code", max_chars=100
                ),
            },
            key="admin_whitepaper_set_codes",
        )
        save_submitted = st.form_submit_button(
            "Save Round 3 questions", type="primary"
        )

    if save_submitted:
        try:
            save_whitepaper_content(
                [
                    {
                        "Question": row["Question"],
                        "Correct answer": row["Correct answer"],
                    }
                    for row in editor_records(edited_questions)
                ],
                [
                    row["Unlock code"]
                    for row in editor_records(edited_codes)
                ],
            )
        except (ValueError, TypeError, KeyError, sqlite3.Error) as error:
            st.error(f"Could not save Round 3 questions: {error}")
        else:
            st.session_state.pop("admin_whitepaper_questions", None)
            st.session_state.pop("admin_whitepaper_set_codes", None)
            st.success("Round 3 questions saved.")
            st.rerun()


def render_visual_puzzle_admin(st: Any) -> None:
    st.subheader(round_title(4))
    st.caption(
        "Upload a pool of at least four images of people, logos, flags, festivals, "
        "or other subjects with accepted answers and 16-digit code segments. "
        "Each team gets four distinct images in a random order. Images must be "
        "JPEG, PNG, or WebP and 5 MB or smaller."
    )
    try:
        puzzles = load_visual_puzzle_content()
    except sqlite3.Error as error:
        st.error(f"Could not load visual puzzle content: {error}")
        return

    if "admin_visual_puzzle_count" not in st.session_state:
        st.session_state.admin_visual_puzzle_count = max(
            VISUAL_PUZZLE_COUNT, len(puzzles)
        )
    puzzle_count = st.number_input(
        "Number of images in the puzzle pool",
        min_value=VISUAL_PUZZLE_COUNT,
        max_value=VISUAL_PUZZLE_MAX_POOL_SIZE,
        step=1,
        key="admin_visual_puzzle_count",
    )
    puzzle_rows = puzzles[:puzzle_count] + [
        {"Image": None, "Correct answer": "", "Code segment": ""}
        for _ in range(max(0, puzzle_count - len(puzzles)))
    ]

    with st.form("admin_visual_puzzles"):
        edited_puzzles = []
        for position, puzzle in enumerate(puzzle_rows, start=1):
            st.markdown(f"**Puzzle {position}**")
            if puzzle["Image"]:
                st.image(puzzle["Image"], width=360)
            uploaded_image = st.file_uploader(
                f"Image {position}",
                type=["jpg", "jpeg", "png", "webp"],
                key=f"admin_visual_image_upload_{position}",
            )
            answer = st.text_input(
                f"Correct answer {position}",
                value=puzzle["Correct answer"],
                max_chars=200,
                key=f"admin_visual_answer_{position}",
            )
            segment = st.text_input(
                f"16-digit code segment {position}",
                value=puzzle["Code segment"],
                max_chars=16,
                key=f"admin_visual_segment_{position}",
            )
            edited_puzzles.append(
                {
                    "Image": (
                        uploaded_image.getvalue()
                        if uploaded_image is not None
                        else puzzle["Image"]
                    ),
                    "Correct answer": answer,
                    "Code segment": segment,
                }
            )
        save_submitted = st.form_submit_button(
            "Save visual puzzles", type="primary"
        )

    if save_submitted:
        try:
            save_visual_puzzle_content(edited_puzzles)
        except (ValueError, TypeError, KeyError, sqlite3.Error) as error:
            st.error(f"Could not save visual puzzles: {error}")
        else:
            for position in range(1, VISUAL_PUZZLE_MAX_POOL_SIZE + 1):
                for field in ("image", "answer", "segment"):
                    st.session_state.pop(f"admin_visual_{field}_{position}", None)
                st.session_state.pop(f"admin_visual_image_upload_{position}", None)
            st.success("Visual puzzle content saved.")
            st.rerun()


def render_hidden_photograph_admin(st: Any) -> None:
    st.subheader(round_title(5))
    st.caption(
        "Upload one or more venue reference photos, each with the code to reveal "
        "after a team submits its matching photo. Images must be JPEG, PNG, or "
        "WebP and 5 MB or smaller. Use camera capture or choose a photo from "
        "your device. Photos are assigned uniquely until the pool is used up; "
        "they may be reused if there are fewer photos than teams."
    )
    try:
        photographs = load_hidden_photograph_content()
    except sqlite3.Error as error:
        st.error(f"Could not load Round 5 photographs: {error}")
        return

    pending_count = st.session_state.pop(
        "admin_hidden_photograph_count_pending", None
    )
    if pending_count is not None:
        st.session_state.admin_hidden_photograph_count = pending_count
    if "admin_hidden_photograph_count" not in st.session_state:
        st.session_state.admin_hidden_photograph_count = max(
            HIDDEN_PHOTOGRAPH_MIN_COUNT, len(photographs)
        )
    photograph_count = st.number_input(
        "Number of reference photographs",
        min_value=HIDDEN_PHOTOGRAPH_MIN_COUNT,
        step=1,
        key="admin_hidden_photograph_count",
    )
    photograph_rows = photographs[:photograph_count] + [
        {"Image": None, "Code": ""}
        for _ in range(max(0, photograph_count - len(photographs)))
    ]

    with st.form("admin_hidden_photographs"):
        edited_photographs = []
        deleted_positions = set()
        for position, photograph in enumerate(photograph_rows, start=1):
            st.markdown(f"**Reference photograph {position}**")
            if photograph["Image"]:
                st.image(photograph["Image"], width=360)
                if st.checkbox(
                    f"Delete reference photograph {position}",
                    key=f"admin_hidden_photo_delete_{position}",
                ):
                    deleted_positions.add(position)
                    continue
            captured_image = st.camera_input(
                f"Take reference photo {position}",
                key=f"admin_hidden_photo_camera_{position}",
            )
            uploaded_image = st.file_uploader(
                f"Choose reference photo {position}",
                type=["jpg", "jpeg", "png", "webp"],
                key=f"admin_hidden_photo_upload_{position}",
            )
            code = st.text_input(
                f"Code revealed for photograph {position}",
                value=photograph["Code"],
                max_chars=200,
                key=f"admin_hidden_photo_code_{position}",
            )
            if captured_image is not None and uploaded_image is not None:
                st.error("Choose either a camera photo or an uploaded photo.")
            edited_photographs.append(
                {
                    "Image": (
                        captured_image.getvalue()
                        if captured_image is not None
                        else (
                            uploaded_image.getvalue()
                            if uploaded_image is not None
                            else photograph["Image"]
                        )
                    ),
                    "Code": code,
                }
            )
        save_submitted = st.form_submit_button(
            "Save Round 5 photographs", type="primary"
        )

    if save_submitted:
        if any(
            st.session_state.get(f"admin_hidden_photo_camera_{position}")
            is not None
            and st.session_state.get(f"admin_hidden_photo_upload_{position}")
            is not None
            for position in range(1, photograph_count + 1)
            if position not in deleted_positions
        ):
            st.error("Choose either a camera photo or an uploaded photo per entry.")
            return
        try:
            save_hidden_photograph_content(edited_photographs)
        except (ValueError, TypeError, KeyError, sqlite3.Error) as error:
            st.error(f"Could not save Round 5 photographs: {error}")
        else:
            for position in range(1, photograph_count + 1):
                for field in ("camera", "upload", "code", "delete"):
                    st.session_state.pop(
                        f"admin_hidden_photo_{field}_{position}", None
                    )
            st.session_state.admin_hidden_photograph_count_pending = len(
                edited_photographs
            )
            st.success("Round 5 photographs saved.")
            st.rerun()


def render_murder_mystery_admin(st: Any) -> None:
    st.subheader(round_title(6))
    st.caption(
        "Edit all 15 cases. Enter one character per line as Name | Role, and "
        "one evidence clue per line. Each hard Sudoku uses digits 1–4 in 2×2 "
        "boxes, with six clues; enter four rows and use 0 for blanks. The culprit "
        "and solution are kept private from participants."
    )
    try:
        cases = load_murder_mystery_content()
    except (ValueError, sqlite3.Error, json.JSONDecodeError) as error:
        st.error(f"Could not load Round 6 content: {error}")
        return

    edited_cases = []
    with st.form("admin_murder_mysteries"):
        for case in cases:
            position = case["position"]
            with st.expander(f"Case {position}: {case['title']}"):
                title = st.text_input(
                    "Case title",
                    value=case["title"],
                    max_chars=200,
                    key=f"admin_mystery_title_{position}",
                )
                briefing = st.text_area(
                    "Murder mystery",
                    value=case["briefing"],
                    max_chars=2000,
                    height=100,
                    key=f"admin_mystery_briefing_{position}",
                )
                characters = st.text_area(
                    "Characters (Name | Role per line)",
                    value="\n".join(
                        f"{character['name']} | {character['role']}"
                        for character in case["characters"]
                    ),
                    max_chars=4000,
                    height=180,
                    key=f"admin_mystery_characters_{position}",
                )
                evidence = st.text_area(
                    "Evidence (one clue per line)",
                    value="\n".join(case["evidence"]),
                    max_chars=4000,
                    height=150,
                    key=f"admin_mystery_evidence_{position}",
                )
                culprit = st.text_input(
                    "Correct culprit",
                    value=case["culprit"],
                    max_chars=100,
                    key=f"admin_mystery_culprit_{position}",
                )
                sudoku_puzzle = st.text_area(
                    "Hard 4x4 Sudoku puzzle (six clues; use 0 for blanks)",
                    value=case["sudoku_puzzle"],
                    max_chars=25,
                    height=110,
                    key=f"admin_mystery_sudoku_puzzle_{position}",
                )
                sudoku_solution = st.text_area(
                    "Sudoku solution (four rows)",
                    value=case["sudoku_solution"],
                    max_chars=25,
                    height=110,
                    key=f"admin_mystery_sudoku_solution_{position}",
                )

                edited_cases.append(
                    {
                        "position": position,
                        "title": title,
                        "briefing": briefing,
                        "characters_text": characters,
                        "evidence_text": evidence,
                        "culprit": culprit,
                        "sudoku_puzzle": sudoku_puzzle,
                        "sudoku_solution": sudoku_solution,
                    }
                )
        save_submitted = st.form_submit_button(
            "Save Round 6 cases", type="primary"
        )

    if save_submitted:
        try:
            normalized_cases = []
            for position, case in enumerate(edited_cases, start=1):
                if position != case["position"]:
                    raise ValueError("Round 6 case positions must remain in order.")
                character_rows = []
                for line_number, line in enumerate(
                    case["characters_text"].splitlines(), start=1
                ):
                    if not line.strip():
                        continue
                    name, separator, role = line.partition("|")
                    if not separator:
                        raise ValueError(
                            f"Case {position}, character line {line_number} "
                            "must use Name | Role."
                        )
                    character_rows.append(
                        {"name": name.strip(), "role": role.strip()}
                    )
                normalized_cases.append(
                    {
                        "position": position,
                        "title": case["title"],
                        "briefing": case["briefing"],
                        "characters": character_rows,
                        "evidence": [
                            line.strip()
                            for line in case["evidence_text"].splitlines()
                            if line.strip()
                        ],
                        "culprit": case["culprit"],
                        "sudoku_puzzle": case["sudoku_puzzle"],
                        "sudoku_solution": case["sudoku_solution"],
                    }
                )
            save_murder_mystery_content(normalized_cases)
        except (ValueError, TypeError, KeyError, sqlite3.Error) as error:
            st.error(f"Could not save Round 6 content: {error}")
        else:
            for case in cases:
                position = case["position"]
                for field in (
                    "title",
                    "briefing",
                    "characters",
                    "evidence",
                    "culprit",
                    "sudoku_puzzle",
                    "sudoku_solution",
                ):
                    st.session_state.pop(
                        f"admin_mystery_{field}_{position}", None
                    )
            st.success(
                "Round 6 cases saved. Team assignments and puzzle progress were reset."
            )
            st.rerun()


def render_hidden_photograph_reviews(st: Any, config: dict[str, Any]) -> None:
    st.subheader(f"{round_title(5)} - Photo submissions")
    penalty_seconds = config["round_penalties"]["5"]["penalty_seconds"]
    try:
        submissions = load_hidden_photograph_submissions()
    except sqlite3.Error as error:
        st.error(f"Could not load Round 5 submissions: {error}")
        return
    if not submissions:
        st.info("No teams have submitted a Round 5 photograph yet.")
        return

    for submission in submissions:
        st.markdown(f"**{submission['team']} — Reference {submission['photograph_position']}**")
        reference_column, submission_column = st.columns(2)
        with reference_column:
            st.caption("Admin reference")
            st.image(submission["reference_image"], width="stretch")
        with submission_column:
            st.caption("Team submission")
            st.image(submission["submission_image"], width="stretch")
        if submission["reviewed_at"] is not None:
            if submission["is_correct"]:
                st.success("Reviewed: correct photo.")
            else:
                st.warning(
                    "Reviewed: incorrect photo. "
                    f"{format_elapsed_time(submission['penalty_seconds'])} penalty applied."
                )
            continue

        correct_column, incorrect_column = st.columns(2)
        if correct_column.button(
            "Mark correct",
            key=f"hidden_photo_correct_{submission['team']}",
        ):
            is_correct = True
        elif incorrect_column.button(
            "Mark incorrect — add "
            f"{format_elapsed_time(penalty_seconds)} penalty",
            key=f"hidden_photo_incorrect_{submission['team']}",
        ):
            is_correct = False
        else:
            continue
        try:
            penalty_seconds = review_hidden_photograph_submission(
                submission["team"], is_correct
            )
        except (ValueError, sqlite3.Error) as error:
            st.error(f"Could not review the photo: {error}")
        else:
            if is_correct:
                st.success("Review saved as correct.")
            elif penalty_seconds:
                st.success(
                    "Review saved; a "
                    f"{format_elapsed_time(penalty_seconds)} penalty was added."
                )
            else:
                st.success("Review saved as incorrect; no penalty was added.")
            st.rerun()


def render_whitepaper_search(
    st: Any, team: str, selected_round: int, rounds: list[int]
) -> None:
    st.subheader(round_title(3))
    st.caption(
        "Answer all four questions in your assigned set. These questions are from "
        "IXO whitepapers."
    )
    try:
        questions, codes = load_whitepaper_content()
    except sqlite3.Error as error:
        st.error(f"Could not load whitepaper questions: {error}")
        return

    questions_incomplete = any(
        not question["Question"] or not question["Correct answer"]
        for question in questions
    )
    codes_incomplete = any(not code for code in codes)
    if questions_incomplete or codes_incomplete:
        st.info(
            "This round is not ready yet. Please ask an admin to configure all "
            "four question sets and their unlock codes."
        )
        return
    try:
        set_number = get_or_assign_whitepaper_set(team, list(range(1, 5)))
    except (ValueError, sqlite3.Error) as error:
        st.error(f"Could not assign a whitepaper question set: {error}")
        return
    first_index = (set_number - 1) * 4
    selected_questions = questions[first_index : first_index + 4]
    with st.form(f"whitepaper_question_set_{set_number}"):
        answers = []
        for question_index, question in enumerate(selected_questions, start=1):
            st.write(f"{question_index}. {question['Question']}")
            answers.append(
                st.text_input(
                    f"Answer {question_index}",
                    key=f"whitepaper_answer_{set_number}_{question_index}",
                )
            )
        submitted = st.form_submit_button("Check all answers", type="primary")

    if submitted:
        if any(not answer.strip() for answer in answers):
            st.warning("Enter an answer for all four questions.")
            return
        try:
            is_correct, unlock_code = check_whitepaper_set_answers(
                set_number, answers
            )
        except (ValueError, sqlite3.Error) as error:
            st.error(f"Could not check the answer: {error}")
            return
        if is_correct and unlock_code:
            try:
                finish_team_round_timer(
                    team,
                    selected_round,
                    unlock_code=unlock_code,
                )
            except (ValueError, sqlite3.Error) as error:
                st.error(f"Could not save Round {selected_round} time: {error}")
                return
            if selected_round == max(rounds):
                try:
                    finish_team_timer(st.session_state.selected_team)
                except (ValueError, sqlite3.Error) as error:
                    st.error(f"Could not finish the team timer: {error}")
                else:
                    st.success("Final round completed!")
            else:
                st.rerun()
        else:
            st.error("Not quite. Review all four questions and try again.")


def render_hotel_detective(st: Any, team: str, config: dict[str, Any]) -> None:
    st.subheader(round_title(2))
    penalty_settings = config["round_penalties"]["2"]
    try:
        statements, codes = load_hotel_detective_content()
        available_sets = [
            set_number
            for set_number in range(1, 5)
            if codes[set_number - 1]
            and all(
                row["Statement"]
                and row["Correct answer"] in ("True", "False")
                for row in statements[
                    (set_number - 1) * 5 : set_number * 5
                ]
            )
        ]
        if not available_sets:
            st.info(
                "This round is not ready yet. Please ask an admin to configure "
                "at least one complete statement set and its unlock code."
            )
            return
        set_number = get_or_assign_hotel_detective_set(team, available_sets)
        progress = get_hotel_detective_progress(team, set_number)
    except (ValueError, sqlite3.Error) as error:
        st.error(f"Could not load Hotel Detective content or progress: {error}")
        return

    st.caption(
        f"Wrong attempts on this set: {progress['wrong_attempts']} "
        f"(first {penalty_settings['free_attempts']} are free). "
        f"Round 2 penalties: {format_elapsed_time(progress['penalty_seconds'])}."
    )
    first_index = (set_number - 1) * 5
    selected_statements = statements[first_index : first_index + 5]
    if any(
        not row["Statement"] or row["Correct answer"] not in ("True", "False")
        for row in selected_statements
    ):
        st.info("This statement set is not ready yet. Please ask an admin to configure it.")
        return
    if not codes[set_number - 1]:
        st.info("This set's unlock code has not been configured yet.")
        return

    with st.form(f"hotel_detective_set_{set_number}"):
        answers = []
        for index, row in enumerate(selected_statements, start=1):
            st.write(f"{index}. {row['Statement']}")
            answers.append(
                st.selectbox(
                    "Choose True or False",
                    ["Select an answer", "True", "False"],
                    key=f"hotel_answer_{set_number}_{index}",
                )
            )
        submitted = st.form_submit_button("Check answers", type="primary")

    if submitted:
        if any(answer == "Select an answer" for answer in answers):
            st.warning("Choose True or False for all five statements.")
            return
        try:
            result = submit_hotel_detective_answers(
                team, set_number, [answer == "True" for answer in answers]
            )
        except (ValueError, sqlite3.Error) as error:
            st.error(f"Could not check the answers: {error}")
            return
        if result["is_correct"] and result["unlock_code"]:
            try:
                finish_team_round_timer(
                    team, 2, unlock_code=result["unlock_code"]
                )
            except (ValueError, sqlite3.Error) as error:
                st.error(f"Could not save Round 2 time: {error}")
                return
            st.rerun()
        else:
            if result["added_penalty"]:
                st.error(
                    "Not quite. A "
                    f"{format_elapsed_time(result['added_penalty'])} "
                    "penalty has been added to your time."
                )
            else:
                st.error("Not quite. Review the statements and try again.")
            st.caption(
                f"Wrong attempts on this set: {result['wrong_attempts']}. "
                f"Accumulated Round 2 penalties: "
                f"{format_elapsed_time(result['penalty_seconds'])}."
            )


def continue_after_visual_puzzle(
    st: Any, team: str, rounds: list[int], config_revision: int
) -> None:
    later_rounds = sorted(round_number for round_number in rounds if round_number > 4)
    if not later_rounds:
        try:
            finish_team_timer(team)
        except (ValueError, sqlite3.Error) as error:
            st.error(f"Could not finish the team timer: {error}")
        else:
            st.success("Final round completed!")
        return

    next_round = later_rounds[0]
    if st.button(f"Continue to {round_title(next_round)}", type="primary"):
        try:
            assigned_place = get_or_assign_place(team, next_round)
        except (OSError, ValueError, json.JSONDecodeError, sqlite3.Error) as error:
            st.error(f"Could not assign a clue: {error}")
            return
        st.session_state.selected_round = next_round
        st.session_state.assigned_place_index = assigned_place
        st.session_state.assignment_revision = config_revision
        st.rerun()


def render_round_unlock_gate(st: Any, team: str, round_number: int) -> bool:
    try:
        code = get_round_unlock_code(team, round_number)
        if code is None:
            st.info(
                "The previous round's entry code is not configured or it has not "
                "been completed. Please ask an organizer for help."
            )
            if st.button("Back to rounds", key=f"round_unlock_back_{round_number}"):
                st.session_state.screen = "round"
                st.rerun()
            return False
        entered = round_unlock_was_entered(team, round_number)
    except (ValueError, sqlite3.Error) as error:
        st.error(f"Could not load the Round {round_number} entry code: {error}")
        return False

    if entered:
        return True

    with st.form(f"round_unlock_{round_number}"):
        submitted_code = st.text_input(
            f"Enter the code from {round_title(round_number - 1)}",
            max_chars=200,
        )
        submitted = st.form_submit_button("Unlock round", type="primary")
    if submitted:
        try:
            is_correct = submit_round_unlock_code(
                team, round_number, submitted_code
            )
        except (ValueError, sqlite3.Error) as error:
            st.error(f"Could not check the Round {round_number} entry code: {error}")
            return False
        if is_correct:
            st.success(f"{round_title(round_number)} unlocked.")
            st.rerun()
        st.error("That code is not correct. Check the code from the previous round.")

    if st.button("Back to rounds", key=f"round_unlock_back_{round_number}"):
        st.session_state.screen = "round"
        st.rerun()
    return False


def render_visual_puzzle(
    st: Any, team: str, rounds: list[int], config_revision: int
) -> None:
    st.subheader(round_title(4))
    try:
        penalty_settings = load_config()["round_penalties"]["4"]
        puzzles = load_visual_puzzle_content()
        if len(puzzles) < VISUAL_PUZZLE_COUNT:
            st.info(
                "This round is not ready yet. Please ask an admin to configure "
                "at least four images."
            )
            return
        progress = get_visual_puzzle_progress(team)
    except (OSError, ValueError, sqlite3.Error) as error:
        st.error(f"Could not load visual puzzle settings or progress: {error}")
        return

    puzzles_by_position = {
        puzzle["Position"]: puzzle for puzzle in puzzles
    }
    assigned_puzzles = [
        puzzles_by_position[position]
        for position in progress["puzzle_positions"]
    ]

    current_position = progress["current_position"]
    if progress["completed"]:
        st.success("Round 4 completed.")
        continue_after_visual_puzzle(st, team, rounds, config_revision)
        return

    for position in range(1, min(current_position, VISUAL_PUZZLE_COUNT + 1)):
        st.success(f"Puzzle {position} solved.")
        st.image(
            create_riddle_image(
                f"Code segment: {assigned_puzzles[position - 1]['Code segment']}"
            ),
            width="stretch",
        )

    if current_position <= VISUAL_PUZZLE_COUNT:
        st.caption(
            f"Puzzle {current_position} of {VISUAL_PUZZLE_COUNT}. "
            f"Wrong attempts on this image: {progress['wrong_attempts']} "
            f"(first {penalty_settings['free_attempts']} are free)."
        )
        st.image(
            assigned_puzzles[current_position - 1]["Image"],
            width="stretch",
        )
        with st.form(f"visual_puzzle_answer_{current_position}"):
            answer = st.text_input("Name what you see")
            submitted = st.form_submit_button("Check answer", type="primary")
        if submitted:
            try:
                result = submit_visual_puzzle_answer(team, answer)
            except (ValueError, sqlite3.Error) as error:
                st.error(f"Could not check the answer: {error}")
                return
            if result["is_correct"]:
                st.rerun()
            if result["penalty_seconds"] > progress["penalty_seconds"]:
                st.error(
                    "Not quite. A "
                    f"{format_elapsed_time(penalty_settings['penalty_seconds'])} "
                    "penalty has been added to your time."
                )
            else:
                st.error("Not quite. Review the image and try again.")
            st.caption(
                f"Wrong attempts on this image: {result['wrong_attempts']}. "
                "Each penalized wrong answer adds "
                f"{format_elapsed_time(penalty_settings['penalty_seconds'])} "
                "to your elapsed time."
            )
        return

    st.write("All four images are solved. Enter the 64-digit code segments in order.")
    with st.form("visual_puzzle_final_code"):
        final_code = st.text_input("64-digit code", max_chars=64)
        submitted = st.form_submit_button("Submit code", type="primary")
    if submitted:
        try:
            is_correct = submit_visual_puzzle_final_code(team, final_code)
        except (ValueError, sqlite3.Error) as error:
            st.error(f"Could not check the final code: {error}")
            return
        if is_correct:
            try:
                finish_team_round_timer(team, 4, unlock_code=final_code)
            except (ValueError, sqlite3.Error) as error:
                st.error(f"Could not save Round 4 time: {error}")
                return
            st.rerun()
        st.error("That code is not correct. Check the segments and their order.")

    st.caption(
        "Accumulated Round 4 penalties: "
        f"{format_elapsed_time(progress['penalty_seconds'])}"
    )


def render_hidden_photograph(st: Any, team: str) -> None:
    st.subheader(round_title(5))
    try:
        state = get_hidden_photograph_state(team)
    except ValueError as error:
        st.info(str(error))
        return
    except sqlite3.Error as error:
        st.error(f"Could not load your Round 5 photograph: {error}")
        return

    st.write(
        "Find this place, then take or upload a photo with every team member "
        "standing with the matching object."
    )
    st.image(state["reference_image"], width="stretch")
    if state["code"] is not None:
        try:
            round_already_completed = get_round_unlock_code(team, 6) is not None
            finish_team_round_timer(team, 5, unlock_code=state["code"])
        except (ValueError, sqlite3.Error) as error:
            st.error(f"Could not save Round 5 time: {error}")
            return
        if not round_already_completed:
            st.rerun()

    if state["reviewed"]:
        if state["is_correct"]:
            st.success("An admin reviewed your photo and marked it correct.")
        else:
            st.warning(
                "An admin reviewed your photo and marked it incorrect. "
                f"A {format_elapsed_time(state['penalty_seconds'])} penalty "
                "has been added to your time."
            )
        return

    if state["submission_image"] is not None:
        st.caption(
            "Your photo is awaiting admin review. You may replace it until "
            "the admin reviews it."
        )
    with st.form("hidden_photograph_submission"):
        captured_image = st.camera_input("Take your team photo")
        uploaded_image = st.file_uploader(
            "Or choose a team photo",
            type=["jpg", "jpeg", "png", "webp"],
        )
        submitted = st.form_submit_button(
            "Submit team photo" if state["submission_image"] is None
            else "Replace team photo",
            type="primary",
        )
    if submitted:
        if captured_image is not None and uploaded_image is not None:
            st.error("Choose either a camera photo or an uploaded photo.")
            return
        image_data = (
            captured_image.getvalue()
            if captured_image is not None
            else uploaded_image.getvalue()
            if uploaded_image is not None
            else None
        )
        if image_data is None:
            st.error("Take or choose a photo before submitting.")
            return
        try:
            code = submit_hidden_photograph(team, image_data)
        except (ValueError, sqlite3.Error) as error:
            st.error(f"Could not submit your photo: {error}")
        else:
            st.success(f"Photo uploaded. Your next code: {code}")
            st.rerun()


def render_murder_mystery(
    st: Any, team: str, rounds: list[int], config: dict[str, Any]
) -> None:
    st.subheader(round_title(6))
    penalty_settings = config["round_penalties"]["6"]
    st.caption(
        "The murder mystery and Sudoku are independent. Work on either one first "
        "or solve them in parallel. There is no attempt cap. Each has "
        f"{penalty_settings['free_attempts']} free wrong attempts; every later "
        "wrong attempt adds "
        f"{format_elapsed_time(penalty_settings['penalty_seconds'])}."
    )
    try:
        state = get_murder_mystery_state(team)
    except (ValueError, sqlite3.Error) as error:
        st.error(f"Could not load your Round 6 puzzles: {error}")
        return

    mystery_column, sudoku_column = st.columns(2)
    with mystery_column:
        case = state["case"]
        mystery_text = "\n".join(
            [
                f"MURDER MYSTERY: {case['title']}",
                "",
                case["briefing"],
                "",
                "CHARACTERS",
                *[
                    f"{character['name']} — {character['role']}"
                    for character in case["characters"]
                ],
                "",
                "EVIDENCE",
                *[
                    f"{position}. {clue}"
                    for position, clue in enumerate(case["evidence"], start=1)
                ],
            ]
        )
        st.image(create_riddle_image(mystery_text), width="stretch")

        if state["mystery_solved"]:
            st.success("Murder mystery solved.")
        else:
            with st.form("murder_mystery_answer"):
                culprit = st.text_input("Who committed the murder?")
                submitted = st.form_submit_button("Submit suspect", type="primary")
            if submitted:
                try:
                    result = submit_murder_mystery_answer(team, "mystery", culprit)
                except (ValueError, sqlite3.Error) as error:
                    st.error(f"Could not check the suspect: {error}")
                else:
                    state["mystery_attempts"] = result["attempts"]
                    state["mystery_solved"] = result["is_correct"]
                    state["penalty_seconds"] = result["penalty_seconds"]
                    if result["is_correct"]:
                        st.success("Correct — you solved the murder mystery.")
                    elif result["added_penalty"]:
                        st.warning(
                            "Incorrect. A "
                            f"{format_elapsed_time(result['added_penalty'])} "
                            "penalty was added."
                        )
                    else:
                        st.error("Not quite. Review the evidence and try again.")
            if not state["mystery_solved"]:
                st.caption(
                    f"Wrong attempts: {state['mystery_attempts']}. "
                    f"First {penalty_settings['free_attempts']} are free."
                )

    with sudoku_column:
        st.markdown("### Sudoku")
        st.caption(
            "Use digits 1–4 with 2×2 boxes. Fill every blank cell; clues are locked."
        )
        if state["sudoku_solved"]:
            st.success("Sudoku solved.")
        else:
            puzzle = case["sudoku_puzzle"]
            puzzle_key = hashlib.sha256(puzzle.encode("ascii")).hexdigest()[:12]
            grid_values: list[list[Optional[int]]] = []
            with st.container(key="round6_sudoku"):
                with st.form("murder_mystery_sudoku"):
                    for row_index in range(SUDOKU_SIZE):
                        grid_row = []
                        cell_columns = st.columns(SUDOKU_SIZE, gap="small")
                        for column_index, column in enumerate(cell_columns):
                            clue = puzzle[
                                row_index * SUDOKU_SIZE + column_index
                            ]
                            with column:
                                value = st.number_input(
                                    f"Row {row_index + 1}, column {column_index + 1}",
                                    min_value=1,
                                    max_value=SUDOKU_SIZE,
                                    value=int(clue) if clue != "0" else None,
                                    step=1,
                                    key=(
                                        f"round6_sudoku_{team}_{puzzle_key}_"
                                        f"{row_index}_{column_index}"
                                    ),
                                    label_visibility="collapsed",
                                    disabled=clue != "0",
                                )
                            grid_row.append(value)
                        grid_values.append(grid_row)
                    submitted = st.form_submit_button(
                        "Check Sudoku", type="primary"
                    )
            if submitted:
                if any(value is None for row in grid_values for value in row):
                    st.warning("Fill every blank cell before checking the Sudoku.")
                else:
                    solution = "".join(
                        str(value) for row in grid_values for value in row
                    )
                    try:
                        result = submit_murder_mystery_answer(
                            team, "sudoku", solution
                        )
                    except (ValueError, sqlite3.Error) as error:
                        st.error(f"Could not check the Sudoku: {error}")
                    else:
                        state["sudoku_attempts"] = result["attempts"]
                        state["sudoku_solved"] = result["is_correct"]
                        state["penalty_seconds"] = result["penalty_seconds"]
                        if result["is_correct"]:
                            st.success("Correct — you solved the Sudoku.")
                        elif result["added_penalty"]:
                            st.warning(
                                "Incorrect. A "
                                f"{format_elapsed_time(result['added_penalty'])} "
                                "penalty was added."
                            )
                        else:
                            st.error("Not quite. Keep working on the Sudoku.")
            if not state["sudoku_solved"]:
                st.caption(
                    f"Wrong attempts: {state['sudoku_attempts']}. "
                    f"First {penalty_settings['free_attempts']} are free."
                )

    st.caption(
        "Round 6 penalties: "
        f"{format_elapsed_time(state['penalty_seconds'])}"
    )
    if state["mystery_solved"] and state["sudoku_solved"]:
        try:
            finish_team_round_timer(team, 6)
            if 6 == max(rounds):
                finish_team_timer(team)
        except (ValueError, sqlite3.Error) as error:
            st.error(f"Could not finish Round 6: {error}")
        else:
            st.success(
                "Both puzzles solved! Final round completed."
                if 6 == max(rounds)
                else "Both puzzles solved! Round 6 completed."
            )


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

    _, logout_column = st.columns([5, 1])
    if logout_column.button("Log out"):
        st.session_state.admin_authenticated = False
        st.rerun()

    admin_section = st.sidebar.radio(
        "Admin menu",
        [
            "Dashboard",
            "Game settings",
            "Teams",
            "Round 1: The Landmark Riddle - Places and coordinates",
            "Round 2: Hotel Detective: Truth or Trick",
            "Round 3: The Whitepaper Cipher",
            "Round 4: The Image Enigma",
            "Round 5: The Hidden Lens",
            "Round 5: The Hidden Lens - Photo submissions",
            "Round 6: The Final Case: Mystery & Sudoku",
        ],
        key="admin_section",
    )

    @st.fragment(run_every="1s")
    def render_team_timing_overview() -> None:
        st.subheader("Team times")
        st.caption(
            "Round times run from first entry to successful completion. "
            "Adjusted total includes penalties."
        )
        try:
            master_frozen, frozen_rounds = get_active_timer_freezes()
            team_summaries = get_admin_team_timing(
                config["teams"], ROUND_NUMBERS
            )
        except sqlite3.Error as error:
            st.error(f"Could not load team times: {error}")
            return

        st.markdown("**Timer freeze controls**")
        st.caption(
            "An individual freeze pauses the selected round and its team's total "
            "while that round is active. The master freeze pauses all team and round timers."
        )
        master_label = "Resume all timers" if master_frozen else "Freeze all timers"
        if st.button(master_label, key="master_timer_freeze"):
            try:
                set_timer_freeze(not master_frozen)
            except sqlite3.Error as error:
                st.error(f"Could not change master freeze: {error}")
            else:
                st.rerun()
        st.caption(
            "Master freeze is active." if master_frozen
            else "Master freeze is inactive. Individual freezes remain in effect."
        )
        team_column, round_column, action_column = st.columns(
            3, vertical_alignment="bottom"
        )
        with team_column:
            selected_team = st.selectbox("Freeze team", config["teams"])
        with round_column:
            selected_round = st.selectbox(
                "Freeze round", ROUND_NUMBERS, format_func=round_title
            )
        individually_frozen = (selected_team, selected_round) in frozen_rounds
        with action_column:
            freeze_label = "Resume selected round" if individually_frozen else "Freeze selected round"
            if st.button(freeze_label, key="individual_timer_freeze"):
                try:
                    set_timer_freeze(not individually_frozen, selected_team, selected_round)
                except (sqlite3.Error, ValueError) as error:
                    st.error(f"Could not change round freeze: {error}")
                else:
                    st.rerun()
        if individually_frozen:
            st.caption("The selected round is individually frozen.")

        started_count = sum(
            summary["elapsed_seconds"] is not None for summary in team_summaries
        )
        finished_count = sum(summary["finished"] for summary in team_summaries)
        in_progress_count = started_count - finished_count
        metric_columns = st.columns(4)
        metric_columns[0].metric("Configured teams", len(team_summaries))
        metric_columns[1].metric("Started", started_count)
        metric_columns[2].metric("In progress", in_progress_count)
        metric_columns[3].metric("Finished", finished_count)

        rows = []
        for summary in team_summaries:
            elapsed = summary["elapsed_seconds"]
            row = {
                "Team": summary["team"],
                "Status": (
                    "Not started"
                    if elapsed is None
                    else "Finished"
                    if summary["finished"]
                    else "In progress"
                ),
                "Elapsed": (
                    "Not started" if elapsed is None else format_elapsed_time(elapsed)
                ),
            }
            for round_summary in summary["rounds"]:
                round_elapsed = round_summary["elapsed_seconds"]
                label = f"Round {round_summary['round_number']}"
                if round_elapsed is None:
                    row[label] = "Not started"
                else:
                    row[label] = format_elapsed_time(round_elapsed)
                    if not round_summary["completed"]:
                        frozen = master_frozen or (
                            summary["team"], round_summary["round_number"]
                        ) in frozen_rounds
                        row[label] += " (frozen)" if frozen else " (in progress)"
            row["Penalties"] = format_elapsed_time(summary["penalty_seconds"])
            adjusted = summary["adjusted_seconds"]
            row["Adjusted total"] = (
                "Not started"
                if adjusted is None
                else format_elapsed_time(adjusted)
            )
            rows.append(row)
        st.dataframe(rows, hide_index=True, width="stretch")

    def save_admin_configuration(
        updated_config: dict[str, Any], widget_keys: tuple[str, ...]
    ) -> None:
        try:
            validate_config(updated_config)
            if updated_config == config:
                st.info("No configuration changes to save.")
                return
            updated_config["config_revision"] = config.get("config_revision", 0) + 1
            clear_assignments()
            save_config(updated_config)
        except (ValueError, TypeError, KeyError, OSError, sqlite3.Error) as error:
            st.error(f"Could not save configuration: {error}")
        else:
            for key in widget_keys:
                st.session_state.pop(key, None)
            st.success("Configuration saved; all round assignments were reset.")
            st.rerun()

    if admin_section == "Dashboard":
        st.caption(
            "A live overview of team progress. Round times run from first entry to "
            "successful completion, and adjusted totals include penalties."
        )
        render_team_timing_overview()
        st.subheader("Puzzle set assignments")
        try:
            assignment_rows = get_admin_set_assignments(config["teams"])
        except sqlite3.Error as error:
            st.error(f"Could not load puzzle set assignments: {error}")
        else:
            st.dataframe(assignment_rows, hide_index=True, width="stretch")
        st.subheader("Reset game progress and time")
        st.warning(
            "A reset permanently clears progress and penalties in the selected scope. "
            "For a round reset, that round and all later rounds are reset, and the "
            "team's overall timer restarts. Earlier rounds, puzzle assignments, "
            "and freeze settings are retained."
        )
        if "reset_success" in st.session_state:
            st.success(st.session_state.pop("reset_success"))
        with st.form("admin_reset_game"):
            reset_scope = st.selectbox(
                "Reset scope", ["Team and round", "Entire team", "All teams"]
            )
            reset_team = st.selectbox("Reset team", config["teams"])
            reset_round = st.selectbox(
                "Reset from round", ROUND_NUMBERS, format_func=round_title
            )
            confirmed = st.checkbox("I understand this reset cannot be undone")
            reset_submitted = st.form_submit_button("Reset game")
        if reset_submitted:
            if not confirmed:
                st.error("Confirm that you understand the reset cannot be undone.")
            else:
                teams = config["teams"] if reset_scope == "All teams" else [reset_team]
                first_round = reset_round if reset_scope == "Team and round" else 1
                try:
                    reset_team_games(teams, first_round)
                except (sqlite3.Error, ValueError) as error:
                    st.error(f"Could not reset game progress: {error}")
                else:
                    st.session_state.reset_success = (
                        "Reset complete. Teams must start their timers again."
                    )
                    st.rerun()
    elif admin_section == "Game settings":
        st.caption("Changes apply to all teams and reset current round assignments.")
        with st.form("admin_game_settings"):
            st.subheader("Game settings")
            settings_column, team_column = st.columns(2)
            with settings_column:
                match_radius = st.number_input(
                    "Coordinate match radius (km)",
                    min_value=0.1,
                    max_value=100.0,
                    value=float(config["match_radius_km"]),
                    step=0.1,
                )
            with team_column:
                team_count = st.number_input(
                    "Number of teams",
                    min_value=1,
                    max_value=MURDER_MYSTERY_CASE_COUNT,
                    value=len(config["teams"]),
                    step=1,
                    key="admin_team_count",
                )
            st.subheader("Global Do's and Don'ts")
            st.caption("Enter one item per line. Leave a field empty to hide that list.")
            instruction_columns = st.columns(2)
            with instruction_columns[0]:
                dos_text = st.text_area(
                    "Do",
                    value="\n".join(config["game_instructions"]["dos"]),
                    max_chars=10_000,
                    key="admin_game_dos",
                )
            with instruction_columns[1]:
                donts_text = st.text_area(
                    "Don't",
                    value="\n".join(config["game_instructions"]["donts"]),
                    max_chars=10_000,
                    key="admin_game_donts",
                )
            st.subheader("Free attempts and penalties")
            st.caption(
                "Free incorrect attempts are counted separately for each statement "
                "set in Round 2, each image in Round 4, and each puzzle in Round 6. "
                "Penalties apply to later wrong answers; Round 5 is penalized when "
                "a photo is marked incorrect."
            )
            penalty_columns = st.columns(4)
            with penalty_columns[0]:
                st.markdown("**Round 2 — Hotel Detective**")
                round2_free_attempts = st.number_input(
                    "Free incorrect attempts per set",
                    min_value=0,
                    max_value=100,
                    value=config["round_penalties"]["2"]["free_attempts"],
                    step=1,
                    key="admin_round2_free_attempts",
                )
                round2_penalty_seconds = st.number_input(
                    "Penalty per later wrong answer (seconds)",
                    min_value=0,
                    max_value=3600,
                    value=config["round_penalties"]["2"]["penalty_seconds"],
                    step=30,
                    key="admin_round2_penalty_seconds",
                )
            with penalty_columns[1]:
                st.markdown("**Round 4 — Image Enigma**")
                round4_free_attempts = st.number_input(
                    "Free incorrect attempts per image",
                    min_value=0,
                    max_value=100,
                    value=config["round_penalties"]["4"]["free_attempts"],
                    step=1,
                    key="admin_round4_free_attempts",
                )
                round4_penalty_seconds = st.number_input(
                    "Penalty per later wrong answer (seconds)",
                    min_value=0,
                    max_value=3600,
                    value=config["round_penalties"]["4"]["penalty_seconds"],
                    step=30,
                    key="admin_round4_penalty_seconds",
                )
            with penalty_columns[2]:
                st.markdown("**Round 5 — The Hidden Lens**")
                st.caption("No free-attempt setting; penalty applies to an incorrect photo review.")
                round5_penalty_seconds = st.number_input(
                    "Penalty for an incorrect photo (seconds)",
                    min_value=0,
                    max_value=3600,
                    value=config["round_penalties"]["5"]["penalty_seconds"],
                    step=30,
                    key="admin_round5_penalty_seconds",
                )
            with penalty_columns[3]:
                st.markdown("**Round 6 — The Final Case**")
                round6_free_attempts = st.number_input(
                    "Free incorrect attempts per puzzle",
                    min_value=0,
                    max_value=100,
                    value=config["round_penalties"]["6"]["free_attempts"],
                    step=1,
                    key="admin_round6_free_attempts",
                )
                round6_penalty_seconds = st.number_input(
                    "Penalty per later wrong answer (seconds)",
                    min_value=0,
                    max_value=3600,
                    value=config["round_penalties"]["6"]["penalty_seconds"],
                    step=30,
                    key="admin_round6_penalty_seconds",
                )
            save_submitted = st.form_submit_button(
                "Save game settings", type="primary"
            )

        if save_submitted:
            updated_config = copy.deepcopy(config)
            updated_config["match_radius_km"] = match_radius
            updated_config["teams"] = team_names_for_count(
                config["teams"], int(team_count)
            )
            updated_config["game_instructions"] = {
                "dos": [
                    instruction.strip()
                    for instruction in dos_text.splitlines()
                    if instruction.strip()
                ],
                "donts": [
                    instruction.strip()
                    for instruction in donts_text.splitlines()
                    if instruction.strip()
                ],
            }
            updated_config["round_penalties"] = {
                "2": {
                    "free_attempts": int(round2_free_attempts),
                    "penalty_seconds": int(round2_penalty_seconds),
                },
                "4": {
                    "free_attempts": int(round4_free_attempts),
                    "penalty_seconds": int(round4_penalty_seconds),
                },
                "5": {"penalty_seconds": int(round5_penalty_seconds)},
                "6": {
                    "free_attempts": int(round6_free_attempts),
                    "penalty_seconds": int(round6_penalty_seconds),
                },
            }
            save_admin_configuration(
                updated_config,
                (
                    "admin_team_count",
                    "admin_game_dos",
                    "admin_game_donts",
                    "admin_round2_free_attempts",
                    "admin_round2_penalty_seconds",
                    "admin_round4_free_attempts",
                    "admin_round4_penalty_seconds",
                    "admin_round5_penalty_seconds",
                    "admin_round6_free_attempts",
                    "admin_round6_penalty_seconds",
                ),
            )
    elif admin_section == "Teams":
        st.caption("Changes apply to all teams and reset current round assignments.")
        with st.form("admin_teams_configuration"):
            st.subheader("Teams")
            st.caption(
                "Replace a default team name with the name provided by that team. "
                "Any unchanged team keeps its default name. Set the team count in "
                "Game settings."
            )
            team_rows = st.data_editor(
                [{"Team name": team} for team in config["teams"]],
                num_rows="fixed",
                hide_index=True,
                column_config={"Team name": st.column_config.TextColumn("Team name")},
                key="admin_teams",
            )
            save_submitted = st.form_submit_button("Save teams", type="primary")

        if save_submitted:
            updated_config = copy.deepcopy(config)
            updated_config["teams"] = [
                str(row["Team name"]).strip()
                for row in editor_records(team_rows)
            ]
            save_admin_configuration(updated_config, ("admin_teams",))
    elif admin_section == "Round 1: The Landmark Riddle - Places and coordinates":
        st.caption("Changes apply to all teams and reset current round assignments.")
        with st.form("admin_round_one_configuration"):
            st.subheader(f"{round_title(1)} - Places and puzzles")
            st.caption(
                "Edit each place name, puzzle, room number, and the Round 2 entry "
                "code that participants find at that location."
            )
            place_rows = [
                {
                    "Place #": index + 1,
                    "Place name": place["name"],
                    "Puzzle": place["puzzle"],
                    "Room number": place["room_number"],
                    "Round 2 entry code": place.get("entry_code", ""),
                }
                for index, place in enumerate(config["places"])
            ]
            edited_place_rows = st.data_editor(
                place_rows,
                hide_index=True,
                disabled=["Place #"],
                column_config={
                    "Place #": st.column_config.NumberColumn("Place #"),
                    "Place name": st.column_config.TextColumn("Place name"),
                    "Puzzle": st.column_config.TextColumn(
                        "Puzzle", max_chars=5000, width="large"
                    ),
                    "Room number": st.column_config.TextColumn(
                        "Room number", max_chars=100
                    ),
                    "Round 2 entry code": st.column_config.TextColumn(
                        "Round 2 entry code", max_chars=200
                    ),
                },
                key="admin_places",
            )

            st.subheader(f"{round_title(1)} - Coordinate references")
            location_rows = []
            location_references = []
            for place_index, place in enumerate(config["places"]):
                for location_index, location in enumerate(place["locations"]):
                    location_rows.append(
                        {
                            "Place #": place_index + 1,
                            "Place": place["name"],
                            "Reference point": location["name"],
                            "Latitude": location["latitude"],
                            "Longitude": location["longitude"],
                        }
                    )
                    location_references.append((place_index, location_index))
            edited_location_rows = st.data_editor(
                location_rows,
                hide_index=True,
                disabled=["Place #", "Place"],
                column_config={
                    "Place #": st.column_config.NumberColumn("Place #"),
                    "Place": st.column_config.TextColumn("Place"),
                    "Reference point": st.column_config.TextColumn(
                        "Reference point"
                    ),
                    "Latitude": st.column_config.NumberColumn(
                        "Latitude", min_value=-90.0, max_value=90.0, format="%.6f"
                    ),
                    "Longitude": st.column_config.NumberColumn(
                        "Longitude",
                        min_value=-180.0,
                        max_value=180.0,
                        format="%.6f",
                    ),
                },
                key="admin_locations",
            )
            save_submitted = st.form_submit_button(
                "Save Round 1 configuration", type="primary"
            )

        if save_submitted:
            updated_config = copy.deepcopy(config)
            place_edits = editor_records(edited_place_rows)
            entry_codes = [
                "" if row["Round 2 entry code"] is None
                else str(row["Round 2 entry code"]).strip()
                for row in place_edits
            ]
            if any(not entry_code for entry_code in entry_codes):
                st.error("Enter a Round 2 entry code for every Round 1 puzzle.")
                return
            for place, row, entry_code in zip(
                updated_config["places"], place_edits, entry_codes
            ):
                place["name"] = str(row["Place name"]).strip()
                place["puzzle"] = str(row["Puzzle"])
                place["room_number"] = str(row["Room number"]).strip()
                place["entry_code"] = entry_code
            for row, (place_index, location_index) in zip(
                editor_records(edited_location_rows), location_references
            ):
                updated_location = updated_config["places"][place_index]["locations"][
                    location_index
                ]
                updated_location["name"] = str(row["Reference point"]).strip()
                updated_location["latitude"] = row["Latitude"]
                updated_location["longitude"] = row["Longitude"]
            save_admin_configuration(
                updated_config, ("admin_places", "admin_locations")
            )
    elif admin_section == "Round 2: Hotel Detective: Truth or Trick":
        render_hotel_detective_admin(st)
    elif admin_section == "Round 3: The Whitepaper Cipher":
        render_whitepaper_search_admin(st)
    elif admin_section == "Round 4: The Image Enigma":
        render_visual_puzzle_admin(st)
    elif admin_section == "Round 5: The Hidden Lens":
        render_hidden_photograph_admin(st)
    elif admin_section == "Round 5: The Hidden Lens - Photo submissions":
        render_hidden_photograph_reviews(st, config)
    elif admin_section == "Round 6: The Final Case: Mystery & Sudoku":
        render_murder_mystery_admin(st)


def main() -> None:
    import streamlit as st

    st.set_page_config(page_title="Treasure Hunt", page_icon="🧭")
    st.markdown(
        """
        <style>
        div[data-testid="stElementContainer"]:has(div[data-testid="stButton"]),
        div[data-testid="stElementContainer"]:has(div[data-testid="stFormSubmitButton"]) {
            width: 100%;
        }

        div[data-testid="stElementContainer"]:has(div[data-testid="stButton"]) > div,
        div[data-testid="stElementContainer"]:has(div[data-testid="stFormSubmitButton"]) > div,
        div[data-testid="stButton"],
        div[data-testid="stFormSubmitButton"],
        div[data-testid="stButton"] > div,
        div[data-testid="stFormSubmitButton"] > div {
            width: 100%;
        }

        div[data-testid="stButton"] > button,
        div[data-testid="stFormSubmitButton"] > button {
            width: 100% !important;
            min-height: 2.75rem;
            padding: 0.65rem 1rem;
            display: flex;
            align-items: center;
            justify-content: center;
            text-align: center;
            white-space: normal;
        }

        div[data-testid="stButton"] > button p,
        div[data-testid="stFormSubmitButton"] > button p {
            margin: 0;
        }

        @media (max-width: 640px) {
            div[data-testid="stHorizontalBlock"]:has(
                > div[data-testid="stColumn"] .st-key-round6_sudoku
            ) {
                flex-direction: column;
            }

            div[data-testid="stHorizontalBlock"]:has(
                > div[data-testid="stColumn"] .st-key-round6_sudoku
            ) > div[data-testid="stColumn"] {
                width: 100% !important;
                min-width: 100% !important;
                flex: 1 1 100% !important;
            }

            .st-key-round6_sudoku div[data-testid="stHorizontalBlock"] {
                gap: 0.25rem;
            }

            .st-key-round6_sudoku div[data-testid="stColumn"] {
                min-width: 0 !important;
            }

            .st-key-round6_sudoku div[data-testid="stNumberInput"] input {
                min-height: 44px;
                padding-left: 0.25rem;
                padding-right: 0.25rem;
                text-align: center;
            }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
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
        for key in (
            "selected_team",
            "selected_round",
            "assigned_place_index",
            "rules_acknowledged",
            "team_reset_revision",
            "round_one_room_reveal",
        ):
            st.session_state.pop(key, None)
        st.session_state.pop("assignment_revision", None)
        st.session_state.screen = "team"

    if st.session_state.screen != "team" and "selected_team" in st.session_state:
        try:
            revision = get_team_reset_revision(st.session_state.selected_team)
        except sqlite3.Error as error:
            st.error(f"Could not check team reset status: {error}")
            return
        if revision != st.session_state.get("team_reset_revision", 0):
            for key in (
                "selected_round",
                "assigned_place_index",
                "assignment_revision",
                "round_one_room_reveal",
            ):
                st.session_state.pop(key, None)
            st.session_state.team_reset_revision = revision
            st.session_state.rules_acknowledged = False
            st.session_state.screen = "instructions"
            st.info("An admin reset your game. Start your timer again to continue.")

    if st.session_state.screen in ("round", "lookup"):
        timer = get_team_timer(st.session_state.selected_team)
        if timer is None or timer[1] is not None:
            st.session_state.screen = "timer"
    if st.session_state.screen in ("instructions", "timer", "round", "lookup"):
        render_team_timer(st, st.session_state.selected_team)
    if st.session_state.screen == "lookup":
        st.caption(round_title(st.session_state.selected_round))
        if (
            st.session_state.selected_round > min(ROUND_NUMBERS)
            and not admin_round_access_enabled(
                st.session_state.get("admin_authenticated", False)
            )
        ):
            if not render_round_unlock_gate(
                st,
                st.session_state.selected_team,
                st.session_state.selected_round,
            ):
                return
        try:
            start_team_round_timer(
                st.session_state.selected_team, st.session_state.selected_round
            )
        except sqlite3.Error as error:
            st.error(f"Could not start the round timer: {error}")
            return

    if (
        st.session_state.screen == "lookup"
        and "assigned_place_index" not in st.session_state
        and st.session_state.selected_round not in (2, 3, 4, 5, 6)
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
        st.subheader("Select your team to log in")
        with st.form("team_selection"):
            selected_team = st.selectbox("Team", config["teams"])
            log_in = st.form_submit_button("Log in", type="primary")
        if log_in:
            st.session_state.selected_team = selected_team
            try:
                st.session_state.team_reset_revision = get_team_reset_revision(selected_team)
            except sqlite3.Error as error:
                st.error(f"Could not check team reset status: {error}")
                return
            st.session_state.rules_acknowledged = False
            st.session_state.screen = "instructions"
            st.rerun()
        return

    st.caption(f"Team: {st.session_state.selected_team}")

    if st.session_state.screen == "instructions":
        st.subheader("Global game instructions")
        st.write(
            "Work together as one team, read each round's instructions carefully, "
            "and enter your answers in the app. Ask an organizer if a rule or clue "
            "is unclear."
        )
        game_instructions = config["game_instructions"]
        if game_instructions["dos"]:
            st.markdown("**Do**")
            st.markdown(
                "\n".join(
                    f"- {instruction}" for instruction in game_instructions["dos"]
                )
            )
        if game_instructions["donts"]:
            st.markdown("**Don't**")
            st.markdown(
                "\n".join(
                    f"- {instruction}" for instruction in game_instructions["donts"]
                )
            )
        st.info(
            "Your timer starts when you continue below and keeps running if you "
            "refresh or leave the page. It stops when your team completes the final round."
        )
        acknowledged = st.checkbox(
            "I have read and understood the rules",
            key="rules_acknowledged",
        )
        team_timer = get_team_timer(st.session_state.selected_team)
        action_label = (
            "Start game timer"
            if team_timer is None
            else "Continue to game"
            if team_timer[1] is None
            else "View completed game"
        )
        if st.button(
            action_label,
            type="primary",
            disabled=not acknowledged,
        ):
            try:
                start_team_timer(st.session_state.selected_team)
            except sqlite3.Error as error:
                st.error(f"Could not start the timer: {error}")
            else:
                st.session_state.screen = "timer"
                st.rerun()
        return

    if st.session_state.screen == "timer":
        timer = get_team_timer(st.session_state.selected_team)
        if timer is None:
            st.session_state.screen = "instructions"
            st.rerun()
        elif timer[1] is not None:
            st.success("This team has completed the treasure hunt.")
        else:
            st.subheader("Timer started")
            st.write("Your timer will continue running if you refresh or leave this page.")
            if st.button("Continue to rounds", type="primary"):
                st.session_state.screen = "round"
                st.rerun()
        return

    if st.session_state.screen == "round":
        st.subheader("Select a round")
        local_admin_access = admin_round_access_enabled(
            st.session_state.get("admin_authenticated", False)
        )
        if local_admin_access:
            unlocked_rounds = ROUND_NUMBERS
            st.info("Local mode: admins can access any round.")
        else:
            try:
                unlocked_rounds = get_unlocked_rounds(
                    st.session_state.selected_team, ROUND_NUMBERS
                )
            except sqlite3.Error as error:
                st.error(f"Could not load round progress: {error}")
                return
            st.caption(
                "Complete each round to unlock the next. You can return to any "
                "unlocked round."
            )
        with st.form("round_selection"):
            selected_round = st.selectbox(
                "Round",
                unlocked_rounds,
                format_func=round_title,
            )
            continue_to_lookup = st.form_submit_button("Continue", type="primary")
        if continue_to_lookup:
            st.session_state.selected_round = selected_round
            if selected_round not in (2, 3, 4, 5, 6):
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
        return

    selected_round = st.session_state.selected_round
    next_rounds = [
        number for number in ROUND_NUMBERS if number > selected_round
    ]
    if next_rounds:
        try:
            next_code = get_round_unlock_code(
                st.session_state.selected_team, min(next_rounds)
            )
        except (ValueError, sqlite3.Error) as error:
            st.error(f"Could not load the next round's entry code: {error}")
            return
        if next_code is not None:
            next_round = min(next_rounds)
            if st.session_state.selected_round != min(ROUND_NUMBERS):
                st.success(
                    f"Code to enter in {round_title(next_round)}: {next_code}"
                )
            if st.button(
                f"Continue to Round {next_round}",
                type="primary",
                key=f"continue_to_round_{next_round}",
            ):
                st.session_state.selected_round = next_round
                st.session_state.screen = "lookup"
                st.rerun()

    if st.session_state.selected_round == 2:
        if st.button("Back to rounds"):
            st.session_state.screen = "round"
            st.rerun()
        render_hotel_detective(
            st, st.session_state.selected_team, config
        )
        return

    if st.session_state.selected_round == 3:
        if st.button("Back to rounds"):
            st.session_state.screen = "round"
            st.rerun()
        render_whitepaper_search(
            st,
            st.session_state.selected_team,
            st.session_state.selected_round,
            ROUND_NUMBERS,
        )
        return

    if st.session_state.selected_round == 4:
        render_visual_puzzle(
            st,
            st.session_state.selected_team,
            ROUND_NUMBERS,
            config.get("config_revision", 0),
        )
        return

    if st.session_state.selected_round == 5:
        if st.button("Back to rounds"):
            st.session_state.screen = "round"
            st.rerun()
        render_hidden_photograph(
            st,
            st.session_state.selected_team,
        )
        return

    if st.session_state.selected_round == 6:
        if st.button("Back to rounds"):
            st.session_state.screen = "round"
            st.rerun()
        render_murder_mystery(
            st,
            st.session_state.selected_team,
            ROUND_NUMBERS,
            config,
        )
        return

    st.image(
        create_riddle_image(
            config["places"][st.session_state.assigned_place_index]["puzzle"]
        ),
        width="stretch",
    )
    if st.button("Back to rounds"):
        st.session_state.screen = "round"
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
            )
            if room_number is None:
                st.warning("Those coordinates do not match your clue. Try again.")
            else:
                place = config["places"][st.session_state.assigned_place_index]
                entry_code = place.get("entry_code", "").strip()
                if not entry_code:
                    st.error(
                        "This Round 1 puzzle's entry code is not configured. "
                        "Please ask an organizer for help."
                    )
                    return
                try:
                    finish_team_round_timer(
                        st.session_state.selected_team,
                        st.session_state.selected_round,
                        unlock_code=entry_code,
                    )
                except (ValueError, sqlite3.Error) as error:
                    st.error(
                        f"Could not save Round {st.session_state.selected_round} time: "
                        f"{error}"
                    )
                    return
                st.session_state.round_one_room_reveal = {
                    "team": st.session_state.selected_team,
                    "place_index": st.session_state.assigned_place_index,
                    "assignment_revision": st.session_state.get(
                        "assignment_revision"
                    ),
                    "room_number": room_number,
                }
                st.rerun()
                if st.session_state.selected_round == max(ROUND_NUMBERS):
                    try:
                        finish_team_timer(st.session_state.selected_team)
                    except (ValueError, sqlite3.Error) as error:
                        st.error(f"Could not finish the team timer: {error}")
                    else:
                        st.success("Final round completed!")
        revealed_room = st.session_state.get("round_one_room_reveal")
        if (
            isinstance(revealed_room, dict)
            and revealed_room.get("team") == st.session_state.selected_team
            and revealed_room.get("place_index")
            == st.session_state.assigned_place_index
            and revealed_room.get("assignment_revision")
            == st.session_state.get("assignment_revision")
        ):
            st.success(f"Room {revealed_room['room_number']}")
        else:
            st.info("Enter the landmark's coordinates to find its room.")


if __name__ == "__main__":
    main()
