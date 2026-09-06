"""
Step 2 - Ingestion
------------------
Pulls live data from api.weather.gov and writes timestamped raw captures
to data/raw/. Raw captures are never modified after they are written -
every downstream stage reads these files, not the API, so any run can be
replayed offline when something breaks further down the pipeline.

    python src/ingest.py                 # full run
    python src/ingest.py --skip-reference  # reuse cached grid metadata

Writes, all sharing one timestamp:
    data/raw/locations_<ts>.json     reference: city -> office/grid/station
    data/raw/observations_<ts>.json  event: latest station readings
    data/raw/forecasts_<ts>.json     event: forecast periods (narrative text)
    data/raw/alerts_<ts>.json        event: active alerts (narrative text)

A capture is only written when it holds at least one record, so a failed
or empty run can never become the "newest" file the loader picks up.

Call budget for 15 cities:
    reference pass : 15 x /points + 15 x /stations       = 30 calls
    every run      : 15 x observations + 15 x forecasts  = 30 calls
                     + 1 /alerts call per distinct state = ~13 calls
Reference data is cached to data/reference_cache.json and refreshed only
with --refresh-reference, because grid cells and station assignments are
slow-changing: re-fetching them daily is wasted quota.
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone

from config import BASE_URL, DATA_DIR, LOCATIONS, RAW_DIR, STATE_NAMES
from nws_client import NWSError, try_get

REFERENCE_CACHE = os.path.join(DATA_DIR, "reference_cache.json")

fetched_at = datetime.now(timezone.utc).isoformat()
timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")


# ==============================
# HELPERS
# ==============================

def save_capture(name, payload, count):
    """Write a raw capture, but only if it actually holds records."""

    if count == 0:
        print(f"  SKIPPED writing {name} - capture was empty.")
        return False

    os.makedirs(RAW_DIR, exist_ok=True)
    path = os.path.join(RAW_DIR, f"{name}_{timestamp}.json")

    with open(path, "w", encoding="utf-8") as file:
        json.dump(payload, file, indent=2)

    print(f"  wrote {path}  ({count} records)")
    return True


def measurement(properties, key):
    """
    NWS wraps every measurement as {'value': x, 'unitCode': ..., ...}
    and 'value' is null more often than you would expect. Flatten it to
    a plain float or None.
    """
    field = properties.get(key)

    if not isinstance(field, dict):
        return None

    value = field.get("value")

    return float(value) if isinstance(value, (int, float)) else None


def section(title):
    print("\n" + "=" * 62)
    print(title)
    print("=" * 62)


# ==============================
# REFERENCE PASS
# ==============================

def fetch_reference():
    """
    Resolve every configured city to its NWS office, grid cell and
    nearest observation station. Slow-changing - cached between runs.
    """

    section("REFERENCE PASS  (/points and /stations)")

    resolved = []

    for location in LOCATIONS:

        print(f"  {location['name']}, {location['state']}")

        points = try_get(f"{BASE_URL}/points/{location['lat']},{location['lon']}")

        if not points:
            print("    could not resolve grid - skipping this city")
            continue

        props = points["properties"]

        record = {
            "idLocation": location["id"],
            "name": location["name"],
            "stateCode": location["state"],
            "stateName": STATE_NAMES.get(location["state"], location["state"]),
            "latitude": location["lat"],
            "longitude": location["lon"],
            "idOffice": props.get("gridId"),
            "gridX": props.get("gridX"),
            "gridY": props.get("gridY"),
            "officeName": (props.get("relativeLocation") or {})
                          .get("properties", {})
                          .get("city"),
            "station": None,
        }

        stations = try_get(
            f"{BASE_URL}/gridpoints/{record['idOffice']}/"
            f"{record['gridX']},{record['gridY']}/stations"
        )

        features = (stations or {}).get("features") or []

        if features:
            # The list is returned nearest-first.
            station_props = features[0]["properties"]
            geometry = features[0].get("geometry") or {}
            coordinates = geometry.get("coordinates") or [None, None]

            record["station"] = {
                "idStation": station_props.get("stationIdentifier"),
                "name": station_props.get("name"),
                "longitude": coordinates[0],
                "latitude": coordinates[1],
            }
            print(f"    office={record['idOffice']} "
                  f"grid={record['gridX']},{record['gridY']} "
                  f"station={record['station']['idStation']}")
        else:
            print(f"    office={record['idOffice']} - no stations found")

        resolved.append(record)

    return resolved


def load_reference_cache():
    if not os.path.exists(REFERENCE_CACHE):
        return None

    try:
        with open(REFERENCE_CACHE, "r", encoding="utf-8") as file:
            data = json.load(file)
    except (ValueError, OSError):
        return None

    return data or None


def save_reference_cache(records):
    os.makedirs(DATA_DIR, exist_ok=True)

    with open(REFERENCE_CACHE, "w", encoding="utf-8") as file:
        json.dump(records, file, indent=2)


# ==============================
# EVENT PASSES
# ==============================

def fetch_observations(locations):
    """Latest reading from each location's station."""

    section("OBSERVATIONS  (/stations/{id}/observations/latest)")

    observations = []

    for location in locations:

        station = location.get("station")

        if not station or not station.get("idStation"):
            continue

        station_id = station["idStation"]

        payload = try_get(f"{BASE_URL}/stations/{station_id}/observations/latest")

        if not payload:
            print(f"  {station_id}: no current observation")
            continue

        props = payload["properties"]
        observed_at = props.get("timestamp")

        if not observed_at:
            print(f"  {station_id}: observation has no timestamp - skipped")
            continue

        observations.append({
            # No id in the payload: derive a deterministic natural key so
            # re-fetching the same reading collides instead of duplicating.
            "idObservation": f"{station_id}@{observed_at}",
            "idStation": station_id,
            "idLocation": location["idLocation"],
            "observedAt": observed_at,
            "temperatureC": measurement(props, "temperature"),
            "dewpointC": measurement(props, "dewpoint"),
            "humidity": measurement(props, "relativeHumidity"),
            "windSpeedKmh": measurement(props, "windSpeed"),
            "windDirection": measurement(props, "windDirection"),
            "pressurePa": measurement(props, "barometricPressure"),
            "visibilityM": measurement(props, "visibility"),
            "textDescription": props.get("textDescription"),
            "fetchedAt": fetched_at,
        })

        temperature = observations[-1]["temperatureC"]
        shown = f"{temperature:.1f}C" if temperature is not None else "n/a"
        print(f"  {station_id}: {shown}  {props.get('textDescription')}")

    return observations


def fetch_forecasts(locations):
    """Forecast periods per location. Carries the narrative text."""

    section("FORECASTS  (/gridpoints/{office}/{x},{y}/forecast)")

    forecasts = []

    for location in locations:

        if not location.get("idOffice"):
            continue

        payload = try_get(
            f"{BASE_URL}/gridpoints/{location['idOffice']}/"
            f"{location['gridX']},{location['gridY']}/forecast"
        )

        if not payload:
            print(f"  {location['name']}: no forecast returned")
            continue

        periods = payload["properties"].get("periods") or []

        for period in periods:

            start = period.get("startTime")

            if not start:
                continue

            forecasts.append({
                "idForecast": f"{location['idLocation']}@{start}",
                "idLocation": location["idLocation"],
                "periodName": period.get("name"),
                "startTime": start,
                "endTime": period.get("endTime"),
                "isDaytime": 1 if period.get("isDaytime") else 0,
                "temperature": period.get("temperature"),
                "temperatureUnit": period.get("temperatureUnit"),
                "windSpeed": period.get("windSpeed"),
                "windDirection": period.get("windDirection"),
                "shortForecast": period.get("shortForecast"),
                "detailedForecast": period.get("detailedForecast"),
                "fetchedAt": fetched_at,
            })

        print(f"  {location['name']}: {len(periods)} periods")

    return forecasts


def fetch_alerts(locations):
    """Active alerts, one call per distinct state. Richest text source."""

    section("ALERTS  (/alerts/active?area={state})")

    states = sorted({location["stateCode"] for location in locations})

    alerts = []

    for state in states:

        payload = try_get(f"{BASE_URL}/alerts/active", params={"area": state})

        features = (payload or {}).get("features") or []

        for feature in features:

            props = feature.get("properties") or {}
            alert_id = props.get("id") or feature.get("id")

            if not alert_id:
                continue

            alerts.append({
                "idAlert": alert_id,
                "stateCode": state,
                "event": props.get("event"),
                "severity": props.get("severity"),
                "urgency": props.get("urgency"),
                "certainty": props.get("certainty"),
                "headline": props.get("headline"),
                "areaDesc": props.get("areaDesc"),
                "effective": props.get("effective"),
                "expires": props.get("expires"),
                "description": props.get("description"),
                "instruction": props.get("instruction"),
                "fetchedAt": fetched_at,
            })

        print(f"  {state}: {len(features)} active")

    # One alert can be returned for several states; dedupe on the CAP id.
    unique = {}
    for alert in alerts:
        unique.setdefault(alert["idAlert"], alert)

    return list(unique.values())


# ==============================
# MAIN
# ==============================

def main():

    parser = argparse.ArgumentParser(description="Ingest live NWS weather data.")
    parser.add_argument(
        "--refresh-reference",
        action="store_true",
        help="Re-resolve city -> office/grid/station instead of using the cache.",
    )
    arguments = parser.parse_args()

    print(f"Run timestamp: {timestamp}")
    print(f"Fetched at:    {fetched_at}")

    # ---- reference (cached) ----

    locations = None if arguments.refresh_reference else load_reference_cache()

    if locations:
        print(f"\nUsing cached reference data for {len(locations)} locations.")
        print("(run with --refresh-reference to re-resolve grids and stations)")
    else:
        locations = fetch_reference()

        if not locations:
            sys.exit(
                "No locations could be resolved. Nothing written - the previous "
                "capture stays the newest one."
            )

        save_reference_cache(locations)

    # ---- events ----

    observations = fetch_observations(locations)
    forecasts = fetch_forecasts(locations)
    alerts = fetch_alerts(locations)

    # ---- persist ----

    section("WRITING RAW CAPTURES")

    save_capture("locations", locations, len(locations))
    save_capture("observations", observations, len(observations))
    save_capture("forecasts", forecasts, len(forecasts))

    # An empty alerts array is a legitimate state of the world (calm
    # weather), not a failure - but writing it would create an empty
    # "newest" capture, so it is skipped and the loader keeps the last
    # non-empty one.
    save_capture("alerts", alerts, len(alerts))

    section("INGESTION COMPLETE")
    print(f"Locations:    {len(locations)}")
    print(f"Observations: {len(observations)}")
    print(f"Forecasts:    {len(forecasts)}")
    print(f"Alerts:       {len(alerts)}")


if __name__ == "__main__":
    try:
        main()
    except NWSError as error:
        sys.exit(f"FATAL: {error}")
