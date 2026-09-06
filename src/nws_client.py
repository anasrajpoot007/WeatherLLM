"""
NWS HTTP client
---------------
One place for every call to api.weather.gov, so the retry, rate-limit
and error policy is defined once and reused by every stage.

Why these choices:
  * NWS requires a User-Agent with contact details instead of an API key.
  * It asks for GeoJSON; the default content type already is, but we
    send Accept explicitly so a server-side default change can't
    silently alter the payload shape.
  * Retries are BOUNDED (5 attempts, exponential backoff capped at 30s).
    An unbounded retry loop turns a dead endpoint into a hung scheduler.
  * HTTP 429 waits and retries WITHOUT consuming the attempt budget:
    the server is asking us to slow down, not reporting a failure.
  * 404 is returned as None rather than retried. It is a legitimate
    answer from NWS (e.g. a station with no recent observation) and
    retrying it just wastes the budget.
"""

import time

import requests

from config import (
    BACKOFF_BASE,
    BACKOFF_MAX,
    MAX_ATTEMPTS,
    RATE_LIMIT_WAIT,
    REQUEST_DELAY,
    REQUEST_TIMEOUT,
    USER_AGENT,
)

HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "application/geo+json",
}


class NWSError(Exception):
    """Raised when a request exhausts its retry budget."""


def get(url, params=None, allow_missing=True):
    """
    GET a NWS URL and return the parsed JSON body.

    Returns None when the resource legitimately does not exist (404) and
    allow_missing is True. Raises NWSError once the attempt budget is
    spent on transport errors or 5xx responses.
    """

    attempt = 0
    last_error = None

    while attempt < MAX_ATTEMPTS:

        try:
            response = requests.get(
                url,
                params=params,
                headers=HEADERS,
                timeout=REQUEST_TIMEOUT,
            )

            # Legitimate "nothing here" - do not burn retries on it.
            if response.status_code == 404:
                if allow_missing:
                    return None
                raise NWSError(f"404 Not Found: {url}")

            # Server asking us to slow down. Does not count as an attempt.
            if response.status_code == 429:
                print(f"    rate limited, waiting {RATE_LIMIT_WAIT}s...")
                time.sleep(RATE_LIMIT_WAIT)
                continue

            # Transient server-side problems are worth retrying.
            if response.status_code >= 500:
                last_error = f"HTTP {response.status_code}"
                attempt += 1

                if attempt >= MAX_ATTEMPTS:
                    break

                wait = min(BACKOFF_BASE * (2 ** (attempt - 1)), BACKOFF_MAX)
                print(f"    {last_error}, retry {attempt}/{MAX_ATTEMPTS} in {wait}s")
                time.sleep(wait)
                continue

            # Any other 4xx is a client bug - failing loudly beats looping.
            if response.status_code >= 400:
                raise NWSError(f"HTTP {response.status_code} for {url}")

            try:
                payload = response.json()
            except ValueError as error:
                raise NWSError(f"Malformed JSON from {url}: {error}")

            time.sleep(REQUEST_DELAY)
            return payload

        except requests.exceptions.RequestException as error:
            last_error = str(error)
            attempt += 1

            if attempt >= MAX_ATTEMPTS:
                break

            wait = min(BACKOFF_BASE * (2 ** (attempt - 1)), BACKOFF_MAX)
            print(f"    connection error, retry {attempt}/{MAX_ATTEMPTS} in {wait}s")
            time.sleep(wait)

    raise NWSError(f"Gave up after {MAX_ATTEMPTS} attempts on {url}: {last_error}")


def try_get(url, params=None):
    """
    Same as get(), but converts an exhausted budget into None instead of
    raising. Used where one failed city should not abort the whole run.
    """
    try:
        return get(url, params=params)
    except NWSError as error:
        print("    SKIPPED:", error)
        return None
