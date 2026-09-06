"""
Step 3 deliverable - integrity checks
-------------------------------------
Run after src/load_data.py. Reports row counts, orphaned foreign keys,
unexpected nulls, duplicate natural keys and text-coverage for the
embedding stage, then exits non-zero if a hard check fails.

    python src/validate.py
"""

import sqlite3
import sys

from config import DB_PATH

conn = sqlite3.connect(DB_PATH)
conn.execute("PRAGMA foreign_keys = ON")

failures = []
warnings = []

TABLES = ("states", "offices", "locations", "stations",
          "observations", "forecasts", "alerts")

# Alerts are legitimately empty when the weather is calm, so an empty
# alerts table is a warning, never a failure.
MAY_BE_EMPTY = {"alerts"}


def section(title):
    print("\n" + "=" * 62)
    print(title)
    print("=" * 62)


# ---------- ROW COUNTS ----------

section("ROW COUNTS")

for table in TABLES:
    count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    print(f"  {table:14} {count}")

    if count == 0:
        if table in MAY_BE_EMPTY:
            warnings.append(f"{table} is empty (no active alerts - normal)")
        else:
            failures.append(f"{table} is empty")


# ---------- ORPHANED FOREIGN KEYS ----------

section("ORPHANED FOREIGN KEYS")

orphan_checks = {
    "offices -> states": """
        SELECT COUNT(*) FROM offices o
        LEFT JOIN states s ON o.stateCode = s.code
        WHERE o.stateCode IS NOT NULL AND s.code IS NULL""",
    "locations -> states": """
        SELECT COUNT(*) FROM locations l
        LEFT JOIN states s ON l.stateCode = s.code
        WHERE l.stateCode IS NOT NULL AND s.code IS NULL""",
    "locations -> offices": """
        SELECT COUNT(*) FROM locations l
        LEFT JOIN offices o ON l.idOffice = o.idOffice
        WHERE l.idOffice IS NOT NULL AND o.idOffice IS NULL""",
    "stations -> locations": """
        SELECT COUNT(*) FROM stations st
        LEFT JOIN locations l ON st.idLocation = l.idLocation
        WHERE st.idLocation IS NOT NULL AND l.idLocation IS NULL""",
    "observations -> stations": """
        SELECT COUNT(*) FROM observations ob
        LEFT JOIN stations st ON ob.idStation = st.idStation
        WHERE st.idStation IS NULL""",
    "forecasts -> locations": """
        SELECT COUNT(*) FROM forecasts f
        LEFT JOIN locations l ON f.idLocation = l.idLocation
        WHERE l.idLocation IS NULL""",
    "alerts -> states": """
        SELECT COUNT(*) FROM alerts a
        LEFT JOIN states s ON a.stateCode = s.code
        WHERE a.stateCode IS NOT NULL AND s.code IS NULL""",
}

for label, query in orphan_checks.items():
    count = conn.execute(query).fetchone()[0]
    print(f"  {label:26} {count}")

    if count:
        failures.append(f"{count} orphaned rows: {label}")

violations = conn.execute("PRAGMA foreign_key_check").fetchall()
print(f"  {'PRAGMA foreign_key_check':26} {len(violations)}")

if violations:
    failures.append(f"{len(violations)} PRAGMA foreign_key_check violations")


# ---------- DUPLICATE NATURAL KEYS ----------

section("DUPLICATE NATURAL KEYS")

key_columns = {
    "locations": "idLocation",
    "stations": "idStation",
    "observations": "idObservation",
    "forecasts": "idForecast",
    "alerts": "idAlert",
}

for table, key in key_columns.items():
    total = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    distinct = conn.execute(f"SELECT COUNT(DISTINCT {key}) FROM {table}").fetchone()[0]
    status = "ok" if total == distinct else "DUPLICATES"
    print(f"  {table:14} rows={total:<6} distinct={distinct:<6} {status}")

    if total != distinct:
        failures.append(f"{table} has duplicate {key}")


# ---------- NULLS ----------

section("NULL COVERAGE (reported, not fatal)")

null_checks = {
    "locations.idOffice": "SELECT COUNT(*) FROM locations WHERE idOffice IS NULL",
    "stations.idLocation": "SELECT COUNT(*) FROM stations WHERE idLocation IS NULL",
    "observations.temperatureC": "SELECT COUNT(*) FROM observations WHERE temperatureC IS NULL",
    "observations.humidity": "SELECT COUNT(*) FROM observations WHERE humidity IS NULL",
    "forecasts.detailedForecast": "SELECT COUNT(*) FROM forecasts WHERE detailedForecast IS NULL OR detailedForecast = ''",
    "alerts.description": "SELECT COUNT(*) FROM alerts WHERE description IS NULL OR description = ''",
    "alerts.instruction": "SELECT COUNT(*) FROM alerts WHERE instruction IS NULL OR instruction = ''",
}

for label, query in null_checks.items():
    count = conn.execute(query).fetchone()[0]
    print(f"  {label:28} {count}")


# ---------- TEXT AVAILABLE TO THE EMBEDDING STAGE ----------

section("EMBEDDABLE TEXT")

narrative = conn.execute(
    """SELECT COUNT(*), AVG(LENGTH(detailedForecast)), MAX(LENGTH(detailedForecast))
       FROM forecasts WHERE detailedForecast IS NOT NULL AND detailedForecast != ''"""
).fetchone()

print(f"  forecast narratives : {narrative[0]} rows, "
      f"avg {narrative[1] or 0:.0f} chars, max {narrative[2] or 0}")

alert_text = conn.execute(
    """SELECT COUNT(*), AVG(LENGTH(description)), MAX(LENGTH(description))
       FROM alerts WHERE description IS NOT NULL AND description != ''"""
).fetchone()

print(f"  alert descriptions  : {alert_text[0]} rows, "
      f"avg {alert_text[1] or 0:.0f} chars, max {alert_text[2] or 0}")

if narrative[0] == 0 and alert_text[0] == 0:
    failures.append("no embeddable text in the database - Step 4 has nothing to run on")


# ---------- EXPLORATORY QUERIES ----------

section("CURRENT CONDITIONS BY LOCATION")

rows = conn.execute(
    """
    SELECT l.name, s.code, st.idStation,
           ROUND(ob.temperatureC, 1), ob.textDescription
    FROM locations l
    JOIN states  s  ON l.stateCode = s.code
    JOIN stations st ON st.idLocation = l.idLocation
    JOIN observations ob ON ob.idStation = st.idStation
    WHERE ob.observedAt = (
        SELECT MAX(o2.observedAt) FROM observations o2 WHERE o2.idStation = st.idStation
    )
    ORDER BY ob.temperatureC DESC
    LIMIT 10
    """
).fetchall()

for name, state, station, temperature, description in rows:
    print(f"  {name+', '+state:22} {station:6} "
          f"{str(temperature)+'C':>8}  {description}")


section("ALERTS BY STATE (4-table join)")

rows = conn.execute(
    """
    SELECT s.name, COUNT(DISTINCT a.idAlert), COUNT(DISTINCT l.idLocation)
    FROM states s
    LEFT JOIN alerts    a ON a.stateCode = s.code
    LEFT JOIN locations l ON l.stateCode = s.code
    GROUP BY s.code
    HAVING COUNT(DISTINCT a.idAlert) > 0
    ORDER BY 2 DESC
    LIMIT 10
    """
).fetchall()

if rows:
    for state, alert_count, location_count in rows:
        print(f"  {state:16} {alert_count:3} alerts, {location_count} tracked cities")
else:
    print("  No active alerts in any tracked state.")


conn.close()


# ---------- RESULT ----------

section("RESULT")

for warning in warnings:
    print("WARN:", warning)

if failures:
    for failure in failures:
        print("FAIL:", failure)
    sys.exit(1)

print("All integrity checks passed.")
