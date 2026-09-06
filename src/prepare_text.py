import sqlite3
import json

conn = sqlite3.connect("sports.db")

query = """
SELECT 
    teams.name AS team,
    leagues.name AS league,
    venues.name AS venue
FROM teams
JOIN leagues ON teams.idLeague = leagues.idLeague
JOIN venues ON teams.idVenue = venues.idVenue
"""

rows = conn.execute(query).fetchall()
conn.close()

documents = []

for team, league, venue in rows:

    text = f"Team: {team}. League: {league}. Stadium: {venue}."

    # Simple chunking
    chunk_size = 100

    for i in range(0, len(text), chunk_size):

        chunk = text[i:i + chunk_size]

        documents.append({
            "text": chunk,
            "metadata": {
                "team": team,
                "league": league,
                "venue": venue,
                "source": "sports_database",
                "chunk_id": i // chunk_size
            }
        })

with open("data/processed_documents.json", "w", encoding="utf-8") as file:
    json.dump(documents, file, indent=4)

print("Text preparation complete!")
print("Chunks:", len(documents))