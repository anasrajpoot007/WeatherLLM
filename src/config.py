"""
Shared configuration
--------------------
Single source of truth for paths, the tracked city list, and the HTTP
contact header the NWS API requires.
"""

import os

# ---------- IDENTITY ----------
# api.weather.gov has no API key. It requires a User-Agent identifying
# the application and a contact address. Override via env var.
CONTACT_EMAIL = os.environ.get("NWS_CONTACT_EMAIL", "muhammadanasrajpoot5@gmail.com")
USER_AGENT = f"weatherLLM/1.0 ({CONTACT_EMAIL})"

BASE_URL = "https://api.weather.gov"


# ---------- PATHS ----------

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DB_PATH = os.path.join(ROOT, "weather.db")
SCHEMA_PATH = os.path.join(ROOT, "sql", "schema.sql")
DATA_DIR = os.path.join(ROOT, "data")
RAW_DIR = os.path.join(DATA_DIR, "raw")
DOCS_JSON = os.path.join(DATA_DIR, "processed_documents.json")
EMBEDDINGS_JSON = os.path.join(DATA_DIR, "embeddings.json")
SEARCH_DOCS_JSON = os.path.join(DATA_DIR, "search_documents.json")
INDEX_PATH = os.path.join(DATA_DIR, "weather.index")
MANIFEST_PATH = os.path.join(DATA_DIR, "run_manifest.json")


# ---------- EMBEDDING MODEL ----------
# Pinned so a silent upgrade cannot change results between runs.
EMBED_MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
EMBED_MODEL_REVISION = "main"
EMBED_DIMENSIONS = 384

# Cosine similarity threshold for "relevant" (vectors are L2-normalised,
# so FAISS inner product == cosine similarity, range -1..1).
SIMILARITY_THRESHOLD = 0.25


# ---------- TRACKED LOCATIONS ----------
# Deliberately spread across NWS forecast offices and states so the
# relational joins have something to say and alerts vary by region.

LOCATIONS = [
    {"id": "seattle-wa",      "name": "Seattle",       "state": "WA", "lat": 47.6062,  "lon": -122.3321},
    {"id": "portland-or",     "name": "Portland",      "state": "OR", "lat": 45.5152,  "lon": -122.6784},
    {"id": "san-francisco-ca","name": "San Francisco", "state": "CA", "lat": 37.7749,  "lon": -122.4194},
    {"id": "los-angeles-ca",  "name": "Los Angeles",   "state": "CA", "lat": 34.0522,  "lon": -118.2437},
    {"id": "denver-co",       "name": "Denver",        "state": "CO", "lat": 39.7392,  "lon": -104.9903},
    {"id": "phoenix-az",      "name": "Phoenix",       "state": "AZ", "lat": 33.4484,  "lon": -112.0740},
    {"id": "dallas-tx",       "name": "Dallas",        "state": "TX", "lat": 32.7767,  "lon": -96.7970},
    {"id": "houston-tx",      "name": "Houston",       "state": "TX", "lat": 29.7604,  "lon": -95.3698},
    {"id": "chicago-il",      "name": "Chicago",       "state": "IL", "lat": 41.8781,  "lon": -87.6298},
    {"id": "minneapolis-mn",  "name": "Minneapolis",   "state": "MN", "lat": 44.9778,  "lon": -93.2650},
    {"id": "atlanta-ga",      "name": "Atlanta",       "state": "GA", "lat": 33.7490,  "lon": -84.3880},
    {"id": "miami-fl",        "name": "Miami",         "state": "FL", "lat": 25.7617,  "lon": -80.1918},
    {"id": "new-york-ny",     "name": "New York",      "state": "NY", "lat": 40.7128,  "lon": -74.0060},
    {"id": "boston-ma",       "name": "Boston",        "state": "MA", "lat": 42.3601,  "lon": -71.0589},
    {"id": "anchorage-ak",    "name": "Anchorage",     "state": "AK", "lat": 61.2181,  "lon": -149.9003},
]

STATE_NAMES = {
    "WA": "Washington",   "OR": "Oregon",       "CA": "California",
    "CO": "Colorado",     "AZ": "Arizona",      "TX": "Texas",
    "IL": "Illinois",     "MN": "Minnesota",    "GA": "Georgia",
    "FL": "Florida",      "NY": "New York",     "MA": "Massachusetts",
    "AK": "Alaska",
}


# ---------- HTTP POLICY ----------

REQUEST_TIMEOUT = 15      # seconds
MAX_ATTEMPTS = 5          # per request before giving up
BACKOFF_BASE = 2          # seconds, doubled each attempt
BACKOFF_MAX = 30          # seconds
RATE_LIMIT_WAIT = 10      # seconds to wait on HTTP 429
REQUEST_DELAY = 0.3       # polite gap between calls
