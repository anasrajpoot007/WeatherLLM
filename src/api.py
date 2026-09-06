"""
WeatherLLM API
--------------
FastAPI backend serving the dashboard and the retrieval layer.

    /api/health      pipeline status
    /api/stats       row counts for the dashboard cards
    /api/locations   tracked cities + latest observation
    /api/locations/{id}  one city: forecast periods + observation history
    /api/observations    recent readings, filterable
    /api/forecasts       forecast periods, filterable
    /api/alerts          active alerts
    /api/states          states with counts
    /api/ask         vector search over the FAISS index

Run from the PROJECT ROOT:

    uvicorn src.api:app --reload

Then open http://127.0.0.1:8000
"""

import json
import sqlite3
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# Allow "uvicorn src.api:app" from the project root to import config.py,
# which lives next to this file.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import (  # noqa: E402
    DB_PATH,
    EMBED_MODEL_NAME,
    EMBED_MODEL_REVISION,
    INDEX_PATH,
    MANIFEST_PATH,
    ROOT,
    SEARCH_DOCS_JSON,
    SIMILARITY_THRESHOLD,
)

FRONTEND_DIR = Path(ROOT) / "frontend"


app = FastAPI(
    title="WeatherLLM API",
    description="Live NWS weather data, relational tables and semantic search.",
    version="2.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------- DATABASE ----------

def get_connection() -> sqlite3.Connection:
    if not Path(DB_PATH).exists():
        raise HTTPException(
            status_code=500,
            detail="Database not found. Run src/create_db.py and src/load_data.py.",
        )

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def query_all(sql: str, params: tuple = ()) -> List[Dict[str, Any]]:
    conn = get_connection()
    try:
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()
    return [dict(row) for row in rows]


# ---------- VECTOR SEARCH (lazy) ----------

class VectorSearchEngine:
    """
    FAISS + sentence-transformers, loaded on the first /api/ask call so
    the server starts instantly. If anything is missing, `available`
    stays False and /api/ask falls back to SQL keyword search.
    """

    def __init__(self) -> None:
        self.index = None
        self.documents: List[Dict[str, Any]] = []
        self.model = None
        self.loaded = False
        self.error: Optional[str] = None

    @property
    def available(self) -> bool:
        return self.loaded and self.error is None

    def load(self) -> None:
        if self.loaded:
            return

        self.loaded = True

        try:
            if not Path(INDEX_PATH).exists() or not Path(SEARCH_DOCS_JSON).exists():
                self.error = (
                    "Vector index not built. Run src/prepare_text.py, "
                    "src/embed.py and src/vector_search.py."
                )
                return

            import faiss
            from sentence_transformers import SentenceTransformer

            self.index = faiss.read_index(str(INDEX_PATH))

            with open(SEARCH_DOCS_JSON, "r", encoding="utf-8") as file:
                store = json.load(file)

            self.documents = store["documents"]

            built_with = store.get("model")

            if built_with and built_with != EMBED_MODEL_NAME:
                self.error = (
                    f"Index built with '{built_with}' but config expects "
                    f"'{EMBED_MODEL_NAME}'. Rebuild the index."
                )
                return

            self.model = SentenceTransformer(
                EMBED_MODEL_NAME, revision=EMBED_MODEL_REVISION
            )

        except Exception as exc:
            self.error = f"{type(exc).__name__}: {exc}"

    def search(self, question: str, top_k: int = 5) -> List[Dict[str, Any]]:
        self.load()

        if not self.available:
            return []

        vector = self.model.encode(
            [question], convert_to_numpy=True, normalize_embeddings=True
        ).astype("float32")

        scores, positions = self.index.search(vector, top_k)

        results = []

        for score, position in zip(scores[0], positions[0]):

            if position < 0 or position >= len(self.documents):
                continue

            document = self.documents[position]

            results.append({
                "text": document.get("text", ""),
                "metadata": document.get("metadata", {}),
                "score": round(float(score), 4),
                "relevant": bool(score >= SIMILARITY_THRESHOLD),
            })

        return results


engine = VectorSearchEngine()


STOP_WORDS = {
    "the", "a", "an", "is", "are", "was", "were", "of", "in", "at", "on",
    "for", "to", "and", "or", "what", "which", "who", "where", "when",
    "will", "it", "be", "does", "do", "did", "tell", "me", "about",
    "from", "their", "its", "please", "show", "any", "there", "going",
    "this", "that", "weather", "forecast",
}


def keyword_fallback(question: str, top_k: int = 5) -> List[Dict[str, Any]]:
    """SQL LIKE search used when the vector index is unavailable."""

    words = [
        word.strip("?.,!'\"").lower()
        for word in question.split()
    ]
    words = [word for word in words if word and word not in STOP_WORDS]

    if not words:
        words = [question.strip().lower()]

    conditions, params = [], []

    for word in words[:6]:
        conditions.append(
            "(f.detailedForecast LIKE ? OR l.name LIKE ? OR f.shortForecast LIKE ?)"
        )
        params.extend([f"%{word}%"] * 3)

    params.append(top_k)

    rows = query_all(
        f"""
        SELECT f.idForecast, f.detailedForecast, f.periodName,
               l.name AS location, l.stateCode
        FROM forecasts f
        JOIN locations l ON f.idLocation = l.idLocation
        WHERE {" OR ".join(conditions)}
        ORDER BY f.startTime
        LIMIT ?
        """,
        tuple(params),
    )

    return [
        {
            "text": f"{row['location']}, {row['stateCode']} - "
                    f"{row['periodName']} forecast: {row['detailedForecast']}",
            "metadata": {
                "source_table": "forecasts",
                "source_id": row["idForecast"],
                "doc_type": "forecast",
                "location": row["location"],
                "state": row["stateCode"],
                "period": row["periodName"],
            },
            "score": None,
            "relevant": True,
        }
        for row in rows
    ]


def build_answer(results: List[Dict[str, Any]]) -> str:
    """A short grounded summary built only from retrieved chunks."""

    if not results:
        return "Nothing in the current weather data answers that."

    lines = []

    for item in results[:3]:
        metadata = item.get("metadata", {})

        if metadata.get("doc_type") == "alert":
            lines.append(
                f"{metadata.get('event')} in effect for "
                f"{metadata.get('area')}."
            )
        else:
            text = item.get("text", "")
            lines.append(text[:220].strip())

    seen = set()
    unique = [line for line in lines if not (line in seen or seen.add(line))]

    return " ".join(unique)


# ---------- MODELS ----------

class AskRequest(BaseModel):
    question: str
    top_k: int = 5


# ---------- ENDPOINTS ----------

@app.get("/api/health")
def health() -> Dict[str, Any]:
    manifest = {}

    if Path(MANIFEST_PATH).exists():
        with open(MANIFEST_PATH, "r", encoding="utf-8") as file:
            manifest = json.load(file)

    return {
        "database": Path(DB_PATH).exists(),
        "vector_index": Path(INDEX_PATH).exists(),
        "vector_engine_loaded": engine.loaded,
        "vector_engine_error": engine.error,
        "manifest": manifest,
    }


@app.get("/api/stats")
def stats() -> Dict[str, Any]:
    conn = get_connection()
    try:
        counts = {
            table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("states", "offices", "locations", "stations",
                          "observations", "forecasts", "alerts")
        }

        latest = conn.execute(
            "SELECT MAX(fetchedAt) FROM observations"
        ).fetchone()[0]

        extremes = conn.execute(
            """
            SELECT MIN(temperatureC), MAX(temperatureC), AVG(temperatureC)
            FROM observations ob
            WHERE ob.observedAt = (
                SELECT MAX(o2.observedAt) FROM observations o2
                WHERE o2.idStation = ob.idStation
            )
            """
        ).fetchone()
    finally:
        conn.close()

    counts["last_fetch"] = latest
    counts["temp_min_c"] = extremes[0]
    counts["temp_max_c"] = extremes[1]
    counts["temp_avg_c"] = round(extremes[2], 1) if extremes[2] is not None else None

    return counts


@app.get("/api/states")
def states() -> List[Dict[str, Any]]:
    return query_all(
        """
        SELECT s.code, s.name,
               COUNT(DISTINCT l.idLocation) AS locations,
               COUNT(DISTINCT a.idAlert)    AS alerts
        FROM states s
        LEFT JOIN locations l ON l.stateCode = s.code
        LEFT JOIN alerts    a ON a.stateCode = s.code
        GROUP BY s.code
        ORDER BY s.name
        """
    )


@app.get("/api/locations")
def locations(
    search: str = Query(""),
    state: Optional[str] = Query(None),
) -> List[Dict[str, Any]]:
    """Tracked cities, each with its most recent observation."""

    sql = """
        SELECT l.idLocation, l.name, l.stateCode, l.latitude, l.longitude,
               l.idOffice, l.gridX, l.gridY,
               s.name AS stateName,
               st.idStation, st.name AS stationName,
               ob.temperatureC, ob.humidity, ob.windSpeedKmh,
               ob.textDescription, ob.observedAt,
               (SELECT COUNT(*) FROM forecasts f
                 WHERE f.idLocation = l.idLocation)  AS forecastCount,
               (SELECT COUNT(*) FROM alerts a
                 WHERE a.stateCode = l.stateCode)    AS alertCount
        FROM locations l
        LEFT JOIN states   s  ON l.stateCode = s.code
        LEFT JOIN stations st ON st.idLocation = l.idLocation
        LEFT JOIN observations ob
               ON ob.idStation = st.idStation
              AND ob.observedAt = (
                    SELECT MAX(o2.observedAt) FROM observations o2
                    WHERE o2.idStation = st.idStation
              )
        WHERE 1 = 1
    """
    params: List[Any] = []

    if search:
        sql += " AND (l.name LIKE ? OR s.name LIKE ? OR l.stateCode LIKE ?)"
        pattern = f"%{search}%"
        params.extend([pattern] * 3)

    if state:
        sql += " AND l.stateCode = ?"
        params.append(state)

    sql += " ORDER BY l.name"

    return query_all(sql, tuple(params))


@app.get("/api/locations/{location_id}")
def location_detail(location_id: str) -> Dict[str, Any]:

    rows = query_all(
        """
        SELECT l.idLocation, l.name, l.stateCode, l.latitude, l.longitude,
               l.idOffice, l.gridX, l.gridY, s.name AS stateName,
               o.name AS officeName
        FROM locations l
        LEFT JOIN states  s ON l.stateCode = s.code
        LEFT JOIN offices o ON l.idOffice  = o.idOffice
        WHERE l.idLocation = ?
        """,
        (location_id,),
    )

    if not rows:
        raise HTTPException(status_code=404, detail="Location not found")

    location = rows[0]

    location["station"] = query_all(
        "SELECT idStation, name, latitude, longitude FROM stations WHERE idLocation = ?",
        (location_id,),
    )

    location["forecasts"] = query_all(
        """
        SELECT idForecast, periodName, startTime, isDaytime, temperature,
               temperatureUnit, windSpeed, windDirection, shortForecast,
               detailedForecast
        FROM forecasts
        WHERE idLocation = ?
        ORDER BY startTime
        LIMIT 14
        """,
        (location_id,),
    )

    location["observations"] = query_all(
        """
        SELECT ob.idObservation, ob.observedAt, ob.temperatureC, ob.humidity,
               ob.windSpeedKmh, ob.textDescription
        FROM observations ob
        JOIN stations st ON ob.idStation = st.idStation
        WHERE st.idLocation = ?
        ORDER BY ob.observedAt DESC
        LIMIT 24
        """,
        (location_id,),
    )

    location["alerts"] = query_all(
        """
        SELECT idAlert, event, severity, headline, areaDesc, expires
        FROM alerts
        WHERE stateCode = ?
        ORDER BY effective DESC
        """,
        (location["stateCode"],),
    )

    return location


@app.get("/api/observations")
def observations(
    location: Optional[str] = Query(None),
    limit: int = Query(200, ge=1, le=2000),
) -> List[Dict[str, Any]]:

    sql = """
        SELECT ob.idObservation, ob.observedAt, ob.temperatureC, ob.dewpointC,
               ob.humidity, ob.windSpeedKmh, ob.windDirection, ob.pressurePa,
               ob.visibilityM, ob.textDescription,
               st.idStation, st.name AS stationName,
               l.idLocation, l.name AS location, l.stateCode
        FROM observations ob
        JOIN stations  st ON ob.idStation = st.idStation
        JOIN locations l  ON st.idLocation = l.idLocation
        WHERE 1 = 1
    """
    params: List[Any] = []

    if location:
        sql += " AND l.idLocation = ?"
        params.append(location)

    sql += " ORDER BY ob.observedAt DESC LIMIT ?"
    params.append(limit)

    return query_all(sql, tuple(params))


@app.get("/api/forecasts")
def forecasts(
    location: Optional[str] = Query(None),
    search: str = Query(""),
    limit: int = Query(200, ge=1, le=2000),
) -> List[Dict[str, Any]]:

    sql = """
        SELECT f.idForecast, f.periodName, f.startTime, f.isDaytime,
               f.temperature, f.temperatureUnit, f.windSpeed, f.windDirection,
               f.shortForecast, f.detailedForecast,
               l.idLocation, l.name AS location, l.stateCode
        FROM forecasts f
        JOIN locations l ON f.idLocation = l.idLocation
        WHERE 1 = 1
    """
    params: List[Any] = []

    if location:
        sql += " AND f.idLocation = ?"
        params.append(location)

    if search:
        sql += """ AND (f.detailedForecast LIKE ?
                        OR f.shortForecast LIKE ?
                        OR l.name LIKE ?)"""
        pattern = f"%{search}%"
        params.extend([pattern] * 3)

    sql += " ORDER BY l.name, f.startTime LIMIT ?"
    params.append(limit)

    return query_all(sql, tuple(params))


@app.get("/api/alerts")
def alerts(
    state: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=1000),
) -> List[Dict[str, Any]]:

    sql = """
        SELECT a.idAlert, a.stateCode, a.event, a.severity, a.urgency,
               a.certainty, a.headline, a.areaDesc, a.effective, a.expires,
               a.description, a.instruction, s.name AS stateName
        FROM alerts a
        LEFT JOIN states s ON a.stateCode = s.code
        WHERE 1 = 1
    """
    params: List[Any] = []

    if state:
        sql += " AND a.stateCode = ?"
        params.append(state)

    sql += " ORDER BY a.effective DESC LIMIT ?"
    params.append(limit)

    return query_all(sql, tuple(params))


@app.post("/api/ask")
def ask(request: AskRequest) -> Dict[str, Any]:

    question = request.question.strip()

    if not question:
        raise HTTPException(status_code=400, detail="Question cannot be empty.")

    results = engine.search(question, top_k=request.top_k)

    if engine.available:
        relevant = [item for item in results if item["relevant"]]

        return {
            "question": question,
            "mode": "vector",
            "answer": build_answer(relevant),
            "results": relevant,
            "considered": results,
            "note": None,
        }

    fallback = keyword_fallback(question, top_k=request.top_k)

    return {
        "question": question,
        "mode": "keyword",
        "answer": build_answer(fallback),
        "results": fallback,
        "considered": fallback,
        "note": engine.error or "Vector index unavailable - using SQL keyword search.",
    }


# ---------- FRONTEND ----------

if FRONTEND_DIR.exists():

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(FRONTEND_DIR / "index.html")

    app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")
