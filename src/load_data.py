"""
Step 3 - ETL: parse, normalise and load
---------------------------------------
Reads the NEWEST non-empty raw capture of each kind and loads it into
weather.db with foreign keys enforced.

Two write strategies, chosen per table for a reason:

  UPSERT (INSERT ... ON CONFLICT DO UPDATE) for reference tables -
  states, offices, locations, stations. A station can be renamed or a
  city reassigned to a different grid cell; we want the current truth.

  APPEND-IF-NEW (INSERT OR IGNORE) for the event tables - observations,
  forecasts, alerts. These are historical facts keyed on a natural key
  (station+timestamp, location+startTime, CAP id). A reading that has
  already been recorded must never be duplicated or rewritten, so the
  table accumulates history across runs.

Either way the load is idempotent: running it twice changes nothing.

    python src/load_data.py
"""

import glob
import json
import os
import sqlite3
import sys

from config import DB_PATH, RAW_DIR, STATE_NAMES


# ---------- PICK THE NEWEST USABLE CAPTURE ----------

def latest_capture(prefix, required=True):
    """
    Return the records from the newest non-empty data/raw/<prefix>_*.json.

    Filenames embed a sortable YYYYmmdd_HHMMSS stamp, so sorted() gives
    true chronological order. glob() alone returns filesystem order,
    which is NOT sorted - relying on it picks an arbitrary file.

    Captures that are empty or unparseable are skipped and the
    next-newest is tried, so one bad run cannot blank the load.
    """

    paths = sorted(glob.glob(os.path.join(RAW_DIR, f"{prefix}_*.json")))

    for path in reversed(paths):

        try:
            with open(path, "r", encoding="utf-8") as file:
                records = json.load(file)
        except (ValueError, OSError) as error:
            print(f"  skipping unreadable capture {path}: {error}")
            continue

        if not records:
            print(f"  skipping empty capture {path}")
            continue

        print(f"  using {os.path.basename(path)}  ({len(records)} records)")
        return records

    if required:
        sys.exit(
            f"No usable '{prefix}' capture in {RAW_DIR}. Run src/ingest.py first."
        )

    print(f"  no '{prefix}' capture found - continuing without it")
    return []


def section(title):
    print("\n" + "=" * 62)
    print(title)
    print("=" * 62)


conn = sqlite3.connect(DB_PATH)
conn.execute("PRAGMA foreign_keys = ON")


# ---------- REFERENCE: states, offices, locations, stations ----------

section("REFERENCE DATA")

locations = latest_capture("locations")

states_seen = {}
offices_seen = {}

for record in locations:
    code = record.get("stateCode")
    if code:
        states_seen[code] = record.get("stateName") or STATE_NAMES.get(code, code)

    office = record.get("idOffice")
    if office:
        offices_seen[office] = (record.get("officeName"), code)

for code, name in states_seen.items():
    conn.execute(
        """INSERT INTO states (code, name) VALUES (?, ?)
           ON CONFLICT(code) DO UPDATE SET name = excluded.name""",
        (code, name),
    )

for office, (name, code) in offices_seen.items():
    conn.execute(
        """INSERT INTO offices (idOffice, name, stateCode) VALUES (?, ?, ?)
           ON CONFLICT(idOffice) DO UPDATE SET
               name = excluded.name,
               stateCode = excluded.stateCode""",
        (office, name, code),
    )

station_rows = 0

for record in locations:

    conn.execute(
        """INSERT INTO locations
           (idLocation, name, stateCode, latitude, longitude, idOffice, gridX, gridY)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(idLocation) DO UPDATE SET
               name      = excluded.name,
               stateCode = excluded.stateCode,
               latitude  = excluded.latitude,
               longitude = excluded.longitude,
               idOffice  = excluded.idOffice,
               gridX     = excluded.gridX,
               gridY     = excluded.gridY""",
        (
            record["idLocation"],
            record.get("name"),
            record.get("stateCode"),
            record.get("latitude"),
            record.get("longitude"),
            record.get("idOffice"),
            record.get("gridX"),
            record.get("gridY"),
        ),
    )

    station = record.get("station") or {}

    if station.get("idStation"):
        conn.execute(
            """INSERT INTO stations
               (idStation, name, latitude, longitude, idLocation)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(idStation) DO UPDATE SET
                   name       = excluded.name,
                   latitude   = excluded.latitude,
                   longitude  = excluded.longitude,
                   idLocation = excluded.idLocation""",
            (
                station["idStation"],
                station.get("name"),
                station.get("latitude"),
                station.get("longitude"),
                record["idLocation"],
            ),
        )
        station_rows += 1

print(f"  states={len(states_seen)} offices={len(offices_seen)} "
      f"locations={len(locations)} stations={station_rows}")


# ---------- EVENTS: observations ----------

section("OBSERVATIONS")

observations = latest_capture("observations", required=False)

known_stations = {
    row[0] for row in conn.execute("SELECT idStation FROM stations").fetchall()
}

inserted = skipped = 0

for record in observations:

    # Never let an orphan through: the FK would reject it anyway, and a
    # clear count is more useful than a stack trace.
    if record.get("idStation") not in known_stations:
        skipped += 1
        continue

    cursor = conn.execute(
        """INSERT OR IGNORE INTO observations
           (idObservation, idStation, observedAt, temperatureC, dewpointC,
            humidity, windSpeedKmh, windDirection, pressurePa, visibilityM,
            textDescription, fetchedAt)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            record["idObservation"],
            record["idStation"],
            record.get("observedAt"),
            record.get("temperatureC"),
            record.get("dewpointC"),
            record.get("humidity"),
            record.get("windSpeedKmh"),
            record.get("windDirection"),
            record.get("pressurePa"),
            record.get("visibilityM"),
            record.get("textDescription"),
            record["fetchedAt"],
        ),
    )
    inserted += cursor.rowcount

print(f"  new rows={inserted}  already present={len(observations) - inserted - skipped}"
      f"  orphaned/skipped={skipped}")


# ---------- EVENTS: forecasts ----------

section("FORECASTS")

forecasts = latest_capture("forecasts", required=False)

known_locations = {
    row[0] for row in conn.execute("SELECT idLocation FROM locations").fetchall()
}

inserted = skipped = 0

for record in forecasts:

    if record.get("idLocation") not in known_locations:
        skipped += 1
        continue

    cursor = conn.execute(
        """INSERT OR IGNORE INTO forecasts
           (idForecast, idLocation, periodName, startTime, endTime, isDaytime,
            temperature, temperatureUnit, windSpeed, windDirection,
            shortForecast, detailedForecast, fetchedAt)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            record["idForecast"],
            record["idLocation"],
            record.get("periodName"),
            record.get("startTime"),
            record.get("endTime"),
            record.get("isDaytime"),
            record.get("temperature"),
            record.get("temperatureUnit"),
            record.get("windSpeed"),
            record.get("windDirection"),
            record.get("shortForecast"),
            record.get("detailedForecast"),
            record["fetchedAt"],
        ),
    )
    inserted += cursor.rowcount

print(f"  new rows={inserted}  already present={len(forecasts) - inserted - skipped}"
      f"  orphaned/skipped={skipped}")


# ---------- EVENTS: alerts ----------

section("ALERTS")

alerts = latest_capture("alerts", required=False)

inserted = 0

for record in alerts:

    # Alerts can name a state we do not otherwise track; register it so
    # the foreign key holds rather than dropping the alert.
    code = record.get("stateCode")

    if code:
        conn.execute(
            "INSERT OR IGNORE INTO states (code, name) VALUES (?, ?)",
            (code, STATE_NAMES.get(code, code)),
        )

    cursor = conn.execute(
        """INSERT OR IGNORE INTO alerts
           (idAlert, stateCode, event, severity, urgency, certainty, headline,
            areaDesc, effective, expires, description, instruction, fetchedAt)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            record["idAlert"],
            code,
            record.get("event"),
            record.get("severity"),
            record.get("urgency"),
            record.get("certainty"),
            record.get("headline"),
            record.get("areaDesc"),
            record.get("effective"),
            record.get("expires"),
            record.get("description"),
            record.get("instruction"),
            record["fetchedAt"],
        ),
    )
    inserted += cursor.rowcount

print(f"  new rows={inserted}  already present={len(alerts) - inserted}")


# ---------- COMMIT ----------

conn.commit()

section("ROW COUNTS")

for table in ("states", "offices", "locations", "stations",
              "observations", "forecasts", "alerts"):
    count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    print(f"  {table:14} {count}")

conn.close()

print("\nLoad complete.")
