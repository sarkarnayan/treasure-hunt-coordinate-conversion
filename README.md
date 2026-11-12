# Treasure Hunt

The Streamlit app guides participants through team selection, starting their
timer, round selection, then a coordinate lookup. The backend currently enables
Rounds 1 and 2.
Each team receives a random riddle rendered as an image. Assignments are saved
locally and unique per round, so two teams do not receive the same place in one
round. Coordinates are matched locally to the assigned landmark and return its
configured room number. A team's elapsed time is saved when they start and is
finished only after a correct coordinate entry in the final configured round.
Refreshing or revisiting the app resumes the team's saved timer.

The supplied logo is displayed in the app header from `assets/inxiteout_logo.jpg`.

## Run locally

```powershell
python -m pip install -r requirements.txt
streamlit run app.py
```

All game configuration is kept in the backend `places.json` file and is not
editable in the participant interface. The `teams` and `rounds` values control
the selection screens, `room_numbers` maps groups to rooms, and
`match_radius_km` controls coordinate matching. Place assignments are stored
separately in `assignments.sqlite3`. Places 1–4 map to room 1, places 5–9 to
room 2, and each following group of five places to the next room.

## Admin

Configure `TREASURE_HUNT_ADMIN_USER` and `TREASURE_HUNT_ADMIN_PASSWORD` as
environment variables on the server before starting Streamlit. Use a strong,
private password; credentials are not stored in the repository. Open **Admin**
in the sidebar and log in to edit the backend JSON configuration. Saving valid
changes resets round assignments so teams receive clues matching the updated
configuration. The 20 place entries must remain in the same order as the
built-in riddles.

For a local PowerShell session, set the variables before running the app:

```powershell
$env:TREASURE_HUNT_ADMIN_USER = "admin"
$env:TREASURE_HUNT_ADMIN_PASSWORD = "choose-a-private-password"
streamlit run app.py
```

Enter latitude and longitude in decimal degrees. Coordinates within the
configured radius of a reference point return its room number. Ajanta Ellora
Caves has reference points for both Ajanta and Ellora. Hampi, Konark's Sun
Temple, and Raniji ki Baori in Bundi are used for the corresponding list entries.

Reference coordinates are approximate. Set an appropriate radius for your use,
particularly when inputs may be recorded away from a landmark's reference point.
The clue appears as an image; as with any on-screen text, this cannot prevent
someone from transcribing or capturing it with a screenshot or camera.
