import sqlite3
import json
import glob

conn = sqlite3.connect("sports.db")
conn.execute("PRAGMA foreign_keys = ON")

# ---------- TEAM DATA ----------
team_files = glob.glob("data/raw/teams_*.json")

with open(team_files[-1], "r", encoding="utf-8") as file:
    data = json.load(file)

for team in data["teams"]:
    conn.execute(
        "INSERT OR REPLACE INTO leagues (idLeague, name) VALUES (?, ?)",
        (team["idLeague"], team["strLeague"])
    )

    conn.execute(
        "INSERT OR REPLACE INTO venues (idVenue, name) VALUES (?, ?)",
        (team["idVenue"], team["strStadium"])
    )

    conn.execute(
        """INSERT OR REPLACE INTO teams
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

with open(player_files[-1], "r", encoding="utf-8") as file:
    data = json.load(file)

for player in data["player"]:
    conn.execute(
        """INSERT OR REPLACE INTO players
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

with open(event_files[-1], "r", encoding="utf-8") as file:
    data = json.load(file)

for event in data["events"]:
    conn.execute(
        """INSERT OR REPLACE INTO events
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

conn.commit()
conn.close()

print("All data loaded successfully!")