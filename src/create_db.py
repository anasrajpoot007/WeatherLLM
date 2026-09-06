"""
Create weather.db from sql/schema.sql.

Safe to re-run: existing tables are left alone unless --reset is passed.

    python src/create_db.py
    python src/create_db.py --reset    # drop and rebuild from scratch
"""

import argparse
import os
import sqlite3

from config import DB_PATH, SCHEMA_PATH

TABLES = (
    "alerts", "forecasts", "observations",
    "stations", "locations", "offices", "states",
)

parser = argparse.ArgumentParser(description="Create the weather database.")
parser.add_argument("--reset", action="store_true", help="Drop existing tables first.")
arguments = parser.parse_args()

conn = sqlite3.connect(DB_PATH)

if arguments.reset:
    # Children before parents so the foreign keys never dangle.
    for table in TABLES:
        conn.execute(f"DROP TABLE IF EXISTS {table}")
    conn.commit()
    print("Dropped existing tables.")

existing = {
    row[0]
    for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'"
    ).fetchall()
}

if existing and not arguments.reset:
    print("Tables already present:", ", ".join(sorted(existing)))
    print("Nothing to do. Use --reset to rebuild from scratch.")
else:
    with open(SCHEMA_PATH, "r", encoding="utf-8") as file:
        conn.executescript(file.read())

    conn.commit()
    print("Database created at", os.path.relpath(DB_PATH))
    print("Tables:", ", ".join(sorted(TABLES)))

conn.close()
