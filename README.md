# Treasure Hunt

The Streamlit app guides participants through team selection, global game rules,
timer start, and round selection. Participants must acknowledge the rules before
starting the timer. The backend currently enables Rounds 1–6:

- Round 1: The Landmark Riddle
- Round 2: Hotel Detective: Truth or Trick
- Round 3: The Whitepaper Cipher
- Round 4: The Image Enigma
- Round 5: The Hidden Lens
- Round 6: The Final Case: Mystery & Sudoku

The rounds combine coordinate lookup, true-or-false statements, whitepaper
questions, image puzzles, a team photo challenge, and a murder mystery with
Sudoku.
Each team receives a random riddle rendered as an image. Assignments are saved
locally and unique per round, so two teams do not receive the same place in one
coordinate round. Coordinates are matched locally to the assigned landmark and
return its configured room number. Hotel Detective presents five True/False
statements at a time; all five must be correct to reveal that set's unlock code.
A team's elapsed time is saved when they start and resumes across refreshes
and screen changes. The timer starts only after the team acknowledges the
global instructions.
Refreshing or revisiting the app resumes the team's saved timer. Each team can
log in from one browser, and a browser stays bound to its chosen team. Admins
can release a team's login in **Admin → Teams** if it needs to move to another
device.

The supplied logo is displayed in the app header from `assets/inxiteout_logo.jpg`.

## Run locally

```powershell
python -m pip install -r requirements.txt
streamlit run app.py
```

Game configuration is managed in the **Admin** section and stored in
`places.json`; it is not editable in the participant interface. Set the team
count in **Game settings**, and edit team names in **Teams**. All six rounds are
always available; `match_radius_km` controls coordinate matching. The team
count is limited to 15 so each team can receive a unique Round 6 mystery. Each
Round 1 place has its own puzzle, coordinate
reference point(s), and room number. Place assignments are stored separately
in `assignments.sqlite3`.

## Admin

Configure `TREASURE_HUNT_ADMIN_USER` and `TREASURE_HUNT_ADMIN_PASSWORD` as
environment variables on the server before starting Streamlit. Use a strong,
private password; credentials are not stored in the repository. Open **Admin**
in the sidebar and log in to open the Admin dashboard. The dashboard shows live
team progress and a **Team times** table with each configured team's elapsed
time, per-round durations, penalties, and adjusted total. Round time starts on
first entry and runs until the team completes that round, including time spent
away. Unstarted teams and rounds are identified in the table. Use the Admin
sidebar menu to navigate between game settings, teams, Round 1 places and
coordinates, the editable round content, and Round 5 photo-submission reviews.
Admin forms manage settings and content without editing raw JSON. Teams select
their default names in the game screen and tell an admin their preferred names;
admins can replace the names in the **Teams** section. Unchanged teams keep
their default names. A team's login can also be released there when changing
devices. **Game settings** also controls Round 4's free wrong answers per image
and penalty per later wrong answer, Round 5's penalty for an incorrect photo
review, and Round 6's free wrong answers per puzzle and penalty per later wrong
answer. Penalties are configured in seconds; zero disables them. Round 5 has no
free-attempt setting because its penalty is applied when an admin reviews a
photo. Enter the 20 statements in order; each
consecutive five statements form a set. Select the correct True/False answer
for each statement, and enter one unlock code per set. Statements and codes are
stored in `assignments.sqlite3` and are blank until configured. Only a complete set with
an unlock code can be played. For Round 3, enter up to 20 whitepaper questions,
their correct answers, and one unlock code per question. Players can select a
configured question; the app identifies the questions as being from IXO
whitepapers and reveals that question's code only for a correct answer. Answers
are checked without regard to capitalization or repeated whitespace. Round
content is stored in `assignments.sqlite3`. Saving valid game configuration
changes resets round assignments so teams receive clues matching the updated
configuration.
For Round 1, edit place names, puzzles, and room numbers in **Round 1: The
Landmark Riddle - Places and puzzles**. Edit each reference point's name and
latitude/longitude in **Round 1: The Landmark Riddle - Coordinate references**.
Each correct coordinate entry reveals the room configured for that assigned
place. The 20 place rows remain fixed.

For Round 4, upload a pool of 4–50 JPEG, PNG, or WebP images (up to 5 MB each),
with a correct answer and a 16-digit code segment for each image. Images can
feature people (such as CEOs or CTOs), logos, flags, festivals, or other
subjects. Each team is assigned four distinct images at random, in a random
order; that selection persists across refreshes. The next image is revealed
only after the current answer is correct. The first three wrong answers per
image are free by default. Every later wrong answer adds a configurable penalty
to the team's
elapsed time. After solving all four assigned images, players enter those four
code segments concatenated in their assigned order as one 64-digit code to
complete the round. Round 4 content, team selections, and progress are stored
in `assignments.sqlite3`. Saving new Round 4 content resets all teams'
Round 4 progress.

For Round 5, configure at least 15 venue reference photos and a code for each
photo in **Round 5: The Hidden Lens**. Admins can capture a reference photo
with their device camera or choose an existing image. Each team is assigned a
different reference photo at random. Teams capture or upload a photo showing
all team members with the matching object; the associated code is revealed
immediately, without waiting for admin review. Teams can replace their photo
until it is reviewed. Admins compare the reference and latest team photo
side-by-side and mark it correct or incorrect. Marking it incorrect adds the
configured penalty to the team's elapsed time. Round 5 photos, assignments,
submissions, and reviews are stored in `assignments.sqlite3`. Saving a new
Round 5 photo pool resets its assignments and submissions.

Round 6 contains 15 murder mysteries, each with at least eight characters,
evidence, and an independent hard 4×4 Sudoku. Configure all cases and Sudokus
in **Admin → Round 6: The Final Case: Mystery & Sudoku**. Sudoku puzzles and solutions use four
rows of four digits; enter `0` for blank cells in a puzzle. The built-in cases
and distinct Sudoku boards (six clues each) seed the editor when Round 6 is
first used.
Each team receives a unique case and Sudoku. The mystery is shown as an image
and the Sudoku has on-screen inputs, so teams can solve them independently and
in parallel. There is no attempt cap. The first three wrong submissions for
each challenge are free by default; every later wrong attempt adds the
configured penalty.
Both must be solved to complete the final round. Round 6 content, team
assignments, progress, and penalties are stored in `assignments.sqlite3`.
Saving case content resets Round 6 assignments and puzzle progress.

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
