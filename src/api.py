"""
SportsLLM API
-------------
FastAPI backend for the SportsLLM frontend.

Serves:
  * /api/stats             -> counts for the dashboard
  * /api/leagues           -> all leagues
  * /api/venues            -> all venues
  * /api/teams             -> teams (+ league + venue + player count), filterable
  * /api/teams/{id}        -> single team metadata + its players
  * /api/events            -> events (+ home/away team, league, venue)
  * /api/players           -> players (+ team), searchable
  * /api/ask               -> vector search over the FAISS index (RAG retrieval)

Run from the PROJECT ROOT (so that sports.db and data/ resolve):

    pip install fastapi uvicorn
    uvicorn src.api:app --reload

Then open http://127.0.0.1:8000
"""

import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# ---------- PATHS ----------

ROOT = Path(__file__).resolve().parent.parent

DB_PATH = ROOT / "sports.db"
INDEX_PATH = ROOT / "data" / "sports.index"
DOCS_PATH = ROOT / "data" / "search_documents.json"
FRONTEND_DIR = ROOT / "frontend"

EMBED_MODEL_NAME = "all-MiniLM-L6-v2"

# Same relevance threshold used by src/search.py (L2 distance)
DISTANCE_THRESHOLD = 1.0


# ---------- APP ----------

app = FastAPI(
    title="SportsLLM API",
    description="Teams, events and vector search over the sports knowledge base.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------- DATABASE ----------

def get_connection() -> sqlite3.Connection:
    if not DB_PATH.exists():
        raise HTTPException(
            status_code=500,
            detail=f"Database not found at {DB_PATH}. Run src/create_db.py and src/load_data.py first.",
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


def table_columns(table: str) -> List[str]:
    """Column names of a table, used for optional-column support."""
    conn = get_connection()
    try:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    finally:
        conn.close()
    return [row["name"] for row in rows]


def event_date_columns() -> Dict[str, Optional[str]]:
    """
    The schema may or may not carry dateEvent / strTime yet.
    Detect it so the API works before and after that migration.
    """
    columns = table_columns("events")
    return {
        "date": "dateEvent" if "dateEvent" in columns else None,
        "time": "strTime" if "strTime" in columns else None,
    }


# ---------- VECTOR SEARCH (lazy loaded) ----------

class VectorSearchEngine:
    """
    Wraps the FAISS index + sentence-transformers model.

    Loaded lazily on the first /api/ask call so the server starts instantly.
    If FAISS, the model, or the index files are unavailable, `available`
    stays False and /api/ask falls back to a SQL keyword search.
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
            if not INDEX_PATH.exists() or not DOCS_PATH.exists():
                self.error = (
                    "Vector index not found. Run src/prepare_text.py, "
                    "src/embed.py and src/vector_search.py to build it."
                )
                return

            import faiss  # noqa: WPS433 (deliberate lazy import)
            from sentence_transformers import SentenceTransformer  # noqa: WPS433

            self.index = faiss.read_index(str(INDEX_PATH))

            with open(DOCS_PATH, "r", encoding="utf-8") as file:
                self.documents = json.load(file)

            self.model = SentenceTransformer(EMBED_MODEL_NAME)

        except Exception as exc:  # pragma: no cover - environment dependent
            self.error = f"{type(exc).__name__}: {exc}"

    def search(self, question: str, top_k: int = 5) -> List[Dict[str, Any]]:
        self.load()

        if not self.available:
            return []

        query_vector = self.model.encode([question])
        distances, indices = self.index.search(query_vector, top_k)

        results = []

        for distance, position in zip(distances[0], indices[0]):

            if position < 0 or position >= len(self.documents):
                continue

            document = self.documents[position]

            results.append(
                {
                    "text": document.get("text", ""),
                    "metadata": document.get("metadata", {}),
                    "distance": round(float(distance), 4),
                    "relevant": bool(distance <= DISTANCE_THRESHOLD),
                }
            )

        return results


engine = VectorSearchEngine()


STOP_WORDS = {
    "the", "a", "an", "is", "are", "was", "were", "of", "in", "at", "on",
    "for", "to", "and", "or", "what", "which", "who", "where", "does",
    "do", "did", "play", "plays", "team", "teams", "tell", "me", "about",
    "from", "their", "his", "her", "its", "please", "show",
}


def keyword_fallback(question: str, top_k: int = 5) -> List[Dict[str, Any]]:
    """
    Plain SQL search used when the vector index is unavailable.

    Matches on individual words rather than the whole sentence, so
    "Which stadium does Everton play at?" still finds Everton.
    """
    words = [
        word.strip("?.,!'\"")
        for word in question.lower().split()
        if word.strip("?.,!'\"") and word.strip("?.,!'\"") not in STOP_WORDS
    ]

    if not words:
        words = [question.strip()]

    conditions = []
    params: List[Any] = []

    for word in words[:6]:
        conditions.append(
            "(teams.name LIKE ? OR leagues.name LIKE ? OR venues.name LIKE ?)"
        )
        params.extend([f"%{word}%"] * 3)

    params.append(top_k)

    rows = query_all(
        f"""
        SELECT teams.name AS team,
               leagues.name AS league,
               venues.name AS venue
        FROM teams
        LEFT JOIN leagues ON teams.idLeague = leagues.idLeague
        LEFT JOIN venues  ON teams.idVenue  = venues.idVenue
        WHERE {" OR ".join(conditions)}
        LIMIT ?
        """,
        tuple(params),
    )

    return [
        {
            "text": f"Team: {row['team']}. League: {row['league']}. Stadium: {row['venue']}.",
            "metadata": {
                "team": row["team"],
                "league": row["league"],
                "venue": row["venue"],
                "source": "sql_keyword_fallback",
            },
            "distance": None,
            "relevant": True,
        }
        for row in rows
    ]


# ---------- MODELS ----------

class AskRequest(BaseModel):
    question: str
    top_k: int = 5


# ---------- ENDPOINTS ----------

@app.get("/api/health")
def health() -> Dict[str, Any]:
    return {
        "database": DB_PATH.exists(),
        "vector_index": INDEX_PATH.exists() and DOCS_PATH.exists(),
        "vector_engine_loaded": engine.loaded,
        "vector_engine_error": engine.error,
        "event_dates": event_date_columns()["date"] is not None,
    }


@app.get("/api/stats")
def stats() -> Dict[str, int]:
    conn = get_connection()
    try:
        counts = {
            table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("teams", "players", "events", "leagues", "venues")
        }
    finally:
        conn.close()
    return counts


@app.get("/api/leagues")
def leagues() -> List[Dict[str, Any]]:
    return query_all(
        """
        SELECT leagues.idLeague   AS id,
               leagues.name       AS name,
               COUNT(teams.idTeam) AS teams
        FROM leagues
        LEFT JOIN teams ON teams.idLeague = leagues.idLeague
        GROUP BY leagues.idLeague, leagues.name
        ORDER BY teams DESC, leagues.name
        """
    )


@app.get("/api/venues")
def venues() -> List[Dict[str, Any]]:
    return query_all(
        """
        SELECT venues.idVenue     AS id,
               venues.name        AS name,
               COUNT(teams.idTeam) AS teams
        FROM venues
        LEFT JOIN teams ON teams.idVenue = venues.idVenue
        GROUP BY venues.idVenue, venues.name
        ORDER BY venues.name
        """
    )


@app.get("/api/teams")
def teams(
    search: str = Query("", description="Match on team, league or venue name"),
    league: Optional[int] = Query(None, description="Filter by idLeague"),
    limit: int = Query(200, ge=1, le=1000),
) -> List[Dict[str, Any]]:

    sql = """
        SELECT teams.idTeam        AS id,
               teams.name          AS name,
               leagues.idLeague    AS league_id,
               leagues.name        AS league,
               venues.idVenue      AS venue_id,
               venues.name         AS venue,
               COUNT(players.idPlayer) AS players
        FROM teams
        LEFT JOIN leagues ON teams.idLeague = leagues.idLeague
        LEFT JOIN venues  ON teams.idVenue  = venues.idVenue
        LEFT JOIN players ON players.idTeam = teams.idTeam
        WHERE 1 = 1
    """
    params: List[Any] = []

    if search:
        sql += """
            AND (teams.name LIKE ?
                 OR leagues.name LIKE ?
                 OR venues.name LIKE ?)
        """
        pattern = f"%{search}%"
        params.extend([pattern, pattern, pattern])

    if league is not None:
        sql += " AND teams.idLeague = ?"
        params.append(league)

    sql += """
        GROUP BY teams.idTeam
        ORDER BY teams.name
        LIMIT ?
    """
    params.append(limit)

    return query_all(sql, tuple(params))


@app.get("/api/teams/{team_id}")
def team_detail(team_id: int) -> Dict[str, Any]:

    rows = query_all(
        """
        SELECT teams.idTeam     AS id,
               teams.name       AS name,
               leagues.idLeague AS league_id,
               leagues.name     AS league,
               venues.idVenue   AS venue_id,
               venues.name      AS venue
        FROM teams
        LEFT JOIN leagues ON teams.idLeague = leagues.idLeague
        LEFT JOIN venues  ON teams.idVenue  = venues.idVenue
        WHERE teams.idTeam = ?
        """,
        (team_id,),
    )

    if not rows:
        raise HTTPException(status_code=404, detail="Team not found")

    team = rows[0]

    team["players"] = query_all(
        """
        SELECT idPlayer AS id, name
        FROM players
        WHERE idTeam = ?
        ORDER BY name
        """,
        (team_id,),
    )

    date_column = event_date_columns()["date"]
    date_select = f"events.{date_column} AS date," if date_column else "NULL AS date,"

    team["events"] = query_all(
        f"""
        SELECT events.idEvent AS id,
               events.name    AS name,
               {date_select}
               home.name      AS home_team,
               away.name      AS away_team,
               venues.name    AS venue
        FROM events
        LEFT JOIN teams home  ON events.idHomeTeam = home.idTeam
        LEFT JOIN teams away  ON events.idAwayTeam = away.idTeam
        LEFT JOIN venues      ON events.idVenue    = venues.idVenue
        WHERE events.idHomeTeam = ? OR events.idAwayTeam = ?
        """,
        (team_id, team_id),
    )

    return team


@app.get("/api/events")
def events(
    search: str = Query(""),
    league: Optional[int] = Query(None),
    limit: int = Query(200, ge=1, le=1000),
) -> List[Dict[str, Any]]:

    columns = event_date_columns()
    date_column = columns["date"]
    time_column = columns["time"]

    date_select = f"events.{date_column} AS date," if date_column else "NULL AS date,"
    time_select = f"events.{time_column} AS time," if time_column else "NULL AS time,"

    sql = f"""
        SELECT events.idEvent   AS id,
               events.name      AS name,
               {date_select}
               {time_select}
               leagues.idLeague AS league_id,
               leagues.name     AS league,
               home.idTeam      AS home_team_id,
               home.name        AS home_team,
               away.idTeam      AS away_team_id,
               away.name        AS away_team,
               venues.idVenue   AS venue_id,
               venues.name      AS venue
        FROM events
        LEFT JOIN leagues    ON events.idLeague   = leagues.idLeague
        LEFT JOIN teams home ON events.idHomeTeam = home.idTeam
        LEFT JOIN teams away ON events.idAwayTeam = away.idTeam
        LEFT JOIN venues     ON events.idVenue    = venues.idVenue
        WHERE 1 = 1
    """
    params: List[Any] = []

    if search:
        sql += """
            AND (events.name LIKE ?
                 OR home.name LIKE ?
                 OR away.name LIKE ?
                 OR venues.name LIKE ?)
        """
        pattern = f"%{search}%"
        params.extend([pattern] * 4)

    if league is not None:
        sql += " AND events.idLeague = ?"
        params.append(league)

    # Sort by date when the column exists, otherwise fall back to the name.
    if date_column:
        sql += f" ORDER BY events.{date_column} IS NULL, events.{date_column}, events.name"
    else:
        sql += " ORDER BY events.name"

    sql += " LIMIT ?"
    params.append(limit)

    return query_all(sql, tuple(params))


@app.get("/api/players")
def players(
    search: str = Query(""),
    team: Optional[int] = Query(None),
    limit: int = Query(100, ge=1, le=1000),
) -> List[Dict[str, Any]]:

    sql = """
        SELECT players.idPlayer AS id,
               players.name     AS name,
               teams.idTeam     AS team_id,
               teams.name       AS team,
               leagues.name     AS league
        FROM players
        LEFT JOIN teams   ON players.idTeam = teams.idTeam
        LEFT JOIN leagues ON teams.idLeague = leagues.idLeague
        WHERE 1 = 1
    """
    params: List[Any] = []

    if search:
        sql += " AND (players.name LIKE ? OR teams.name LIKE ?)"
        pattern = f"%{search}%"
        params.extend([pattern, pattern])

    if team is not None:
        sql += " AND players.idTeam = ?"
        params.append(team)

    sql += " ORDER BY players.name LIMIT ?"
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
            "answer": build_answer(question, relevant),
            "results": relevant,
            "considered": results,
            "note": None,
        }

    fallback = keyword_fallback(question, top_k=request.top_k)

    return {
        "question": question,
        "mode": "keyword",
        "answer": build_answer(question, fallback),
        "results": fallback,
        "considered": fallback,
        "note": engine.error or "Vector index unavailable - using SQL keyword search.",
    }


def build_answer(question: str, results: List[Dict[str, Any]]) -> str:
    """A short, grounded summary sentence built only from retrieved chunks."""
    if not results:
        return "No relevant result found in the knowledge base for that question."

    lines = []

    for item in results[:3]:
        metadata = item.get("metadata", {})
        team = metadata.get("team")
        league = metadata.get("league")
        venue = metadata.get("venue")

        if team:
            lines.append(f"{team} play in the {league} at {venue}.")
        else:
            lines.append(item.get("text", "").strip())

    # De-duplicate while preserving order (chunking can repeat a team).
    seen = set()
    unique = [line for line in lines if not (line in seen or seen.add(line))]

    return " ".join(unique)


# ---------- FRONTEND ----------

if FRONTEND_DIR.exists():

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(FRONTEND_DIR / "index.html")

    app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")
