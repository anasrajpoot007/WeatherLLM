"""
Step 1 - API reconnaissance
---------------------------
Pulls one real response from every endpoint the pipeline uses and prints
its shape: which fields are scalar, which are nested, which are arrays,
and which look like natural keys.

    python src/reconnaissance.py

Writes docs/sample_responses.json so the data contract in the README can
quote real payloads rather than the documentation's abbreviated examples.
"""

import json
import os

from config import BASE_URL, LOCATIONS, ROOT
from nws_client import get

SAMPLE = LOCATIONS[0]
OUT_PATH = os.path.join(ROOT, "docs", "sample_responses.json")


def describe(value, indent=2):
    """Print the shape of a JSON value, one line per key."""

    pad = " " * indent

    if isinstance(value, dict):
        for key, item in value.items():
            if isinstance(item, dict):
                print(f"{pad}{key}: object ({len(item)} keys)")
            elif isinstance(item, list):
                kind = type(item[0]).__name__ if item else "empty"
                print(f"{pad}{key}: array[{len(item)}] of {kind}")
            else:
                preview = str(item)
                if len(preview) > 60:
                    preview = preview[:57] + "..."
                print(f"{pad}{key}: {type(item).__name__} = {preview}")
    else:
        print(f"{pad}{type(value).__name__}")


def section(title):
    print("\n" + "=" * 62)
    print(title)
    print("=" * 62)


samples = {}

# ---------- 1. POINTS ----------

section(f"1. /points/{SAMPLE['lat']},{SAMPLE['lon']}  (coordinate -> grid)")

points = get(f"{BASE_URL}/points/{SAMPLE['lat']},{SAMPLE['lon']}")
samples["points"] = points

props = points["properties"]
describe(props)

print("\n  NATURAL KEYS: gridId (forecast office), gridX + gridY (grid cell)")
print("  NESTED: relativeLocation -> city/state, geometry -> coordinates")

office = props["gridId"]
grid_x = props["gridX"]
grid_y = props["gridY"]


# ---------- 2. STATIONS ----------

section(f"2. /gridpoints/{office}/{grid_x},{grid_y}/stations")

stations = get(f"{BASE_URL}/gridpoints/{office}/{grid_x},{grid_y}/stations")
samples["stations"] = {"features": stations["features"][:2]}

print(f"  features: array[{len(stations['features'])}]")
describe(stations["features"][0]["properties"], indent=4)

station_id = stations["features"][0]["properties"]["stationIdentifier"]
print(f"\n  NATURAL KEY: stationIdentifier = {station_id}")


# ---------- 3. LATEST OBSERVATION ----------

section(f"3. /stations/{station_id}/observations/latest")

observation = get(f"{BASE_URL}/stations/{station_id}/observations/latest")
samples["observation"] = observation

if observation:
    obs = observation["properties"]
    describe(obs)
    print("\n  MESSY: every measurement is a nested object")
    print("         {'value': 12.2, 'unitCode': 'wmoUnit:degC', 'qualityControl': 'V'}")
    print("         value is frequently null - the ETL must tolerate it.")
    print("  NATURAL KEY: none supplied -> derive from station + timestamp")
else:
    print("  No recent observation for this station (a legitimate 404).")


# ---------- 4. FORECAST ----------

section(f"4. /gridpoints/{office}/{grid_x},{grid_y}/forecast")

forecast = get(f"{BASE_URL}/gridpoints/{office}/{grid_x},{grid_y}/forecast")
samples["forecast"] = {
    "properties": {
        "updated": forecast["properties"].get("updated"),
        "periods": forecast["properties"]["periods"][:2],
    }
}

periods = forecast["properties"]["periods"]
print(f"  periods: array[{len(periods)}]")
describe(periods[0], indent=4)

narrative = periods[0].get("detailedForecast", "")
print(f"\n  TEXT FOR EMBEDDING: detailedForecast, {len(narrative)} chars")
print(f"    \"{narrative[:150]}...\"")
print("  NATURAL KEY: none supplied -> derive from location + startTime")


# ---------- 5. ALERTS ----------

section(f"5. /alerts/active?area={SAMPLE['state']}")

alerts = get(f"{BASE_URL}/alerts/active", params={"area": SAMPLE["state"]})
samples["alerts"] = {"features": alerts["features"][:1]}

features = alerts["features"]
print(f"  features: array[{len(features)}]")

if features:
    describe(features[0]["properties"], indent=4)

    body = features[0]["properties"].get("description") or ""
    print(f"\n  TEXT FOR EMBEDDING: description, {len(body)} chars")
    print(f"    \"{body[:150]}...\"")
    print("  NATURAL KEY: properties.id (CAP identifier)")
else:
    print(f"  No active alerts in {SAMPLE['state']} right now.")
    print("  This is normal and the pipeline must handle an empty array.")


# ---------- SAVE ----------

os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)

with open(OUT_PATH, "w", encoding="utf-8") as file:
    json.dump(samples, file, indent=2)

section("DONE")
print("Sample payloads written to docs/sample_responses.json")
