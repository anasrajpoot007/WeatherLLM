import requests
import json
import time
from datetime import datetime

LEAGUE_ID = "4328"
BASE_URL = "https://www.thesportsdb.com/api/v1/json/123"

timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")


# ---------- DISCOVER TEAMS ----------

url = f"{BASE_URL}/lookup_all_teams.php"

response = requests.get(
    url,
    params={"id": LEAGUE_ID},
    timeout=10
)

print("Teams Status:", response.status_code)

team_data = response.json()
teams = team_data.get("teams", [])

print("Teams discovered:", len(teams))


# ---------- GET EVENTS FROM TEAMS ----------

all_events = {}

for team in teams:

    team_id = team["idTeam"]

    response = requests.get(
        f"{BASE_URL}/eventsnext.php",
        params={"id": team_id},
        timeout=10
    )

    print("Events:", team["strTeam"], response.status_code)

    if response.status_code == 200:

        data = response.json()

        for event in data.get("events", []):

            all_events[event["idEvent"]] = event

    time.sleep(2)


events = list(all_events.values())

print("Unique events found:", len(events))


# Save events

with open(
    f"data/raw/events_{timestamp}.json",
    "w",
    encoding="utf-8"
) as file:
    json.dump(events, file, indent=4)


# ---------- GET TEAMS FROM EVENTS ----------

team_ids = set()

for event in events:

    team_ids.add(event["idHomeTeam"])
    team_ids.add(event["idAwayTeam"])


print("Teams from events:", len(team_ids))


event_teams = []

for team_id in team_ids:

    response = requests.get(
        f"{BASE_URL}/lookupteam.php",
        params={"id": team_id},
        timeout=10
    )

    print("Team:", team_id, response.status_code)

    data = response.json()

    if data.get("teams"):
        event_teams.append(data["teams"][0])

    time.sleep(2)


with open(
    f"data/raw/teams_{timestamp}.json",
    "w",
    encoding="utf-8"
) as file:
    json.dump({"teams": event_teams}, file, indent=4)


print("Event teams saved:", len(event_teams))


# ---------- GET PLAYERS ----------

players = []

for team in event_teams:

    team_id = team["idTeam"]

    response = requests.get(
        f"{BASE_URL}/lookup_all_players.php",
        params={"id": team_id},
        timeout=10
    )

    print("Players:", team["strTeam"], response.status_code)

    if response.status_code == 200:

        data = response.json()

        players.extend(data.get("player", []))

    time.sleep(2)


with open(
    f"data/raw/players_{timestamp}.json",
    "w",
    encoding="utf-8"
) as file:
    json.dump(players, file, indent=4)


print("Players saved:", len(players))

print("\nDynamic ingestion completed!")