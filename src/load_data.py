import sqlite3
import json
import glob
import requests

conn = sqlite3.connect("sports.db")
conn.execute("PRAGMA foreign_keys = ON")


# ---------- HELPER: ADD TEAM ----------

def add_team(team_id):

    # Check if team already exists
    result = conn.execute(
        "SELECT idTeam FROM teams WHERE idTeam = ?",
        (team_id,)
    ).fetchone()

    if result:
        return

    # Fetch team from API
    url = "https://www.thesportsdb.com/api/v1/json/123/lookupteam.php"
    response = requests.get(
        url,
        params={"id": team_id},
        timeout=10
    )

    data = response.json()

    if not data.get("teams"):
        print("Could not find team:", team_id)
        return

    team = data["teams"][0]

    # Add League first
    conn.execute(
        """INSERT OR IGNORE INTO leagues
        (idLeague, name)
        VALUES (?, ?)""",
        (
            team["idLeague"],
            team["strLeague"]
        )
    )

    # Add Venue
    if team.get("idVenue"):
        conn.execute(
            """INSERT OR IGNORE INTO venues
            (idVenue, name)
            VALUES (?, ?)""",
            (
                team["idVenue"],
                team["strStadium"]
            )
        )

    # Add Team
    conn.execute(
        """INSERT OR IGNORE INTO teams
        (idTeam, name, idLeague, idVenue)
        VALUES (?, ?, ?, ?)""",
        (
            team["idTeam"],
            team["strTeam"],
            team["idLeague"],
            team["idVenue"]
        )
    )

    print("Added team:", team["strTeam"])


# ---------- TEAM DATA ----------

team_files = glob.glob("data/raw/teams_*.json")
team_file = team_files[-1]

with open(team_file, "r", encoding="utf-8") as file:
    data = json.load(file)

for team in data["teams"]:

    # League
    conn.execute(
        """INSERT OR IGNORE INTO leagues
        (idLeague, name)
        VALUES (?, ?)""",
        (
            team["idLeague"],
            team["strLeague"]
        )
    )

    # Venue
    if team.get("idVenue"):
        conn.execute(
            """INSERT OR IGNORE INTO venues
            (idVenue, name)
            VALUES (?, ?)""",
            (
                team["idVenue"],
                team["strStadium"]
            )
        )

    # Team
    conn.execute(
        """INSERT OR IGNORE INTO teams
        (idTeam, name, idLeague, idVenue)
        VALUES (?, ?, ?, ?)""",
        (
            team["idTeam"],
            team["strTeam"],
            team["idLeague"],
            team["idVenue"]
        )
    )


# ---------- PLAYER DATA ----------

player_files = glob.glob("data/raw/players_*.json")
player_file = player_files[-1]

with open(player_file, "r", encoding="utf-8") as file:
    data = json.load(file)

for player in data:

    # Make sure player's team exists
    add_team(player["idTeam"])

    conn.execute(
        """INSERT OR IGNORE INTO players
        (idPlayer, name, idTeam)
        VALUES (?, ?, ?)""",
        (
            player["idPlayer"],
            player["strPlayer"],
            player["idTeam"]
        )
    )


# ---------- EVENT DATA ----------

event_files = glob.glob("data/raw/events_*.json")
event_file = event_files[-1]

with open(event_file, "r", encoding="utf-8") as file:
    data = json.load(file)

for event in data:

    # Make sure Home Team exists
    add_team(event["idHomeTeam"])

    # Make sure Away Team exists
    add_team(event["idAwayTeam"])

    # Make sure Event League exists
    league_result = conn.execute(
        "SELECT idLeague FROM leagues WHERE idLeague = ?",
        (event["idLeague"],)
    ).fetchone()

    if league_result is None:

        conn.execute(
            """INSERT OR IGNORE INTO leagues
            (idLeague, name)
            VALUES (?, ?)""",
            (
                event["idLeague"],
                "Unknown League"
            )
        )

    # Make sure Event Venue exists
    venue_result = conn.execute(
        "SELECT idVenue FROM venues WHERE idVenue = ?",
        (event["idVenue"],)
    ).fetchone()

    if venue_result is None:

        conn.execute(
            """INSERT OR IGNORE INTO venues
            (idVenue, name)
            VALUES (?, ?)""",
            (
                event["idVenue"],
                event.get("strVenue", "Unknown Venue")
            )
        )

    # Insert Event
    conn.execute(
        """INSERT OR IGNORE INTO events
        (idEvent, name, idLeague, idHomeTeam, idAwayTeam, idVenue)
        VALUES (?, ?, ?, ?, ?, ?)""",
        (
            event["idEvent"],
            event["strEvent"],
            event["idLeague"],
            event["idHomeTeam"],
            event["idAwayTeam"],
            event["idVenue"]
        )
    )

    print("Event added:", event["strEvent"])


# ---------- SAVE ----------

conn.commit()
conn.close()

print("\nData saved to database!")