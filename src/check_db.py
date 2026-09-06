import sqlite3

conn = sqlite3.connect("sports.db")

query = """
SELECT 
    events.name,
    teams.name AS home_team,
    leagues.name AS league,
    venues.name AS venue
FROM events
JOIN teams ON events.idHomeTeam = teams.idTeam
JOIN leagues ON events.idLeague = leagues.idLeague
JOIN venues ON events.idVenue = venues.idVenue;
"""

rows = conn.execute(query).fetchall()

for row in rows:
    print(row)

conn.close()