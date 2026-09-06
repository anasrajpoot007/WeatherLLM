"""
Step 4a - Chunking and metadata
--------------------------------
Turns the narrative text in the database into a chunked, metadata-tagged
corpus ready for embedding.

WHAT GETS EMBEDDED, AND WHY
    forecasts.detailedForecast  - NWS forecaster prose, ~150-400 chars
    alerts.description          - CAP advisory body, 300-5000+ chars
    alerts.instruction          - "what you should do" prose

    Numeric columns (temperature, humidity, pressure) are deliberately
    NOT embedded. Embedding a stringified number produces a vector that
    encodes the words, not the magnitude - "72 degrees" and "27 degrees"
    land close together while meaning opposite things. Numbers belong in
    SQL, where they can be filtered and compared. The dashboard queries
    them directly; the vector index answers questions about language.

CHUNKING STRATEGY
    Chosen per document type, because the two text sources have very
    different shapes:

    Forecast narratives are 1-3 sentences and already one coherent
    thought. Splitting them would separate "high near 75" from the day
    it belongs to, so they are kept WHOLE - one chunk per period.

    Alert bodies are long, multi-paragraph, and often cover several
    distinct instructions. They are split with a SLIDING WINDOW of
    CHUNK_WORDS words and CHUNK_OVERLAP words of overlap, so a sentence
    spanning a boundary still appears intact in one of the two chunks.
    Splitting happens on paragraph boundaries first and only falls back
    to the word window when a paragraph is itself too long.

    Alternatives considered: fixed character windows (splits mid-word
    and ignores structure), and sentence-level chunks (too small here -
    single forecast sentences lack the location context that makes them
    retrievable). Both are worse for this data.

CONTEXTUAL PREFIX
    Every chunk is prefixed with a short header naming the location,
    state and period - e.g. "Seattle, WA - Tonight forecast:". Without
    it, "Sunny with a high near 75" is unattributable: the same sentence
    appears verbatim for a dozen cities and retrieval cannot tell them
    apart. The prefix is part of the embedded text AND is recorded in
    metadata.

TRACEABILITY
    Every chunk carries source_table and source_id - the actual primary
    key of the row it came from - so any retrieved result can be joined
    straight back to weather.db. chunk_index/chunk_count locate it
    within its source document.

    python src/prepare_text.py
"""

import json
import re
import sqlite3
from datetime import datetime, timezone

from config import DB_PATH, DOCS_JSON

# Sliding window for long documents (alerts).
CHUNK_WORDS = 120
CHUNK_OVERLAP = 30

# Documents shorter than this stay whole.
MIN_CHUNK_WORDS = 40


def clean(text):
    """
    NWS alert bodies are hard-wrapped at ~68 columns for teletype
    output. Collapse single newlines inside a paragraph but keep blank
    lines, which are real paragraph breaks.
    """
    if not text:
        return ""

    text = text.replace("\r\n", "\n").replace("\r", "\n")
    paragraphs = re.split(r"\n\s*\n", text)

    cleaned = []
    for paragraph in paragraphs:
        collapsed = re.sub(r"\s*\n\s*", " ", paragraph).strip()
        collapsed = re.sub(r"[ \t]{2,}", " ", collapsed)
        if collapsed:
            cleaned.append(collapsed)

    return "\n\n".join(cleaned)


def window(words, size, overlap):
    """Yield overlapping word windows."""
    step = max(size - overlap, 1)

    for start in range(0, len(words), step):
        piece = words[start:start + size]

        if not piece:
            break

        yield piece

        if start + size >= len(words):
            break


def split_document(text):
    """
    Split a long document into chunks: paragraph-first, word-window as
    the fallback for paragraphs that are still too long. Returns a list
    of strings.
    """
    text = clean(text)

    if not text:
        return []

    if len(text.split()) <= CHUNK_WORDS:
        return [text]

    chunks = []
    buffer = []
    buffer_len = 0

    for paragraph in text.split("\n\n"):

        words = paragraph.split()

        # A single oversized paragraph gets the sliding window.
        if len(words) > CHUNK_WORDS:

            if buffer:
                chunks.append(" ".join(buffer))
                buffer, buffer_len = [], 0

            for piece in window(words, CHUNK_WORDS, CHUNK_OVERLAP):
                chunks.append(" ".join(piece))
            continue

        # Otherwise pack paragraphs until the window is full.
        if buffer_len + len(words) > CHUNK_WORDS and buffer:
            chunks.append(" ".join(buffer))
            buffer, buffer_len = [], 0

        buffer.extend(words)
        buffer_len += len(words)

    if buffer:
        tail = " ".join(buffer)

        # Avoid emitting a stub; fold it into the previous chunk.
        if chunks and len(buffer) < MIN_CHUNK_WORDS:
            chunks[-1] = chunks[-1] + " " + tail
        else:
            chunks.append(tail)

    return chunks


def emit(documents, prefix, body, metadata):
    """Chunk one source document and append its chunks to `documents`."""

    pieces = split_document(body)

    for index, piece in enumerate(pieces):
        documents.append({
            "text": f"{prefix} {piece}",
            "metadata": {
                **metadata,
                "context_prefix": prefix,
                "chunk_index": index,
                "chunk_count": len(pieces),
                "chunk_words": len(piece.split()),
            },
        })


conn = sqlite3.connect(DB_PATH)
conn.row_factory = sqlite3.Row

documents = []


# ---------- FORECAST NARRATIVES ----------

rows = conn.execute(
    """
    SELECT f.idForecast, f.idLocation, f.periodName, f.startTime,
           f.shortForecast, f.detailedForecast,
           f.temperature, f.temperatureUnit,
           l.name AS location, l.stateCode, s.name AS stateName
    FROM forecasts f
    JOIN locations l ON f.idLocation = l.idLocation
    LEFT JOIN states s ON l.stateCode = s.code
    WHERE f.detailedForecast IS NOT NULL AND f.detailedForecast != ''
    ORDER BY l.name, f.startTime
    """
).fetchall()

for row in rows:

    prefix = (
        f"{row['location']}, {row['stateCode']} - "
        f"{row['periodName']} forecast:"
    )

    emit(
        documents,
        prefix,
        row["detailedForecast"],
        {
            "source_table": "forecasts",
            "source_id": row["idForecast"],       # <- joins back to weather.db
            "doc_type": "forecast",
            "location_id": row["idLocation"],
            "location": row["location"],
            "state": row["stateCode"],
            "state_name": row["stateName"],
            "period": row["periodName"],
            "start_time": row["startTime"],
            "short_forecast": row["shortForecast"],
            "temperature": row["temperature"],
            "temperature_unit": row["temperatureUnit"],
        },
    )

forecast_chunks = len(documents)
print(f"Forecast narratives : {len(rows)} rows -> {forecast_chunks} chunks")


# ---------- ALERT TEXT ----------

rows = conn.execute(
    """
    SELECT a.idAlert, a.stateCode, a.event, a.severity, a.urgency,
           a.headline, a.areaDesc, a.effective, a.expires,
           a.description, a.instruction,
           s.name AS stateName
    FROM alerts a
    LEFT JOIN states s ON a.stateCode = s.code
    ORDER BY a.effective DESC
    """
).fetchall()

alert_rows = 0

for row in rows:

    base = {
        "source_table": "alerts",
        "source_id": row["idAlert"],              # <- joins back to weather.db
        "doc_type": "alert",
        "state": row["stateCode"],
        "state_name": row["stateName"],
        "event": row["event"],
        "severity": row["severity"],
        "urgency": row["urgency"],
        "headline": row["headline"],
        "area": row["areaDesc"],
        "effective": row["effective"],
        "expires": row["expires"],
    }

    prefix = f"{row['event']} for {row['areaDesc']} ({row['stateCode']}):"

    if row["description"]:
        emit(documents, prefix, row["description"],
             {**base, "field": "description"})
        alert_rows += 1

    if row["instruction"]:
        emit(documents, f"{prefix} what to do -", row["instruction"],
             {**base, "field": "instruction"})

alert_chunks = len(documents) - forecast_chunks
print(f"Alert text          : {alert_rows} alerts -> {alert_chunks} chunks")

conn.close()


# ---------- SAVE ----------

if not documents:
    raise SystemExit(
        "No text found to chunk. Run src/ingest.py and src/load_data.py first."
    )

payload = {
    "generated_at": datetime.now(timezone.utc).isoformat(),
    "chunking": {
        "forecast": "whole document (1-3 sentences, already coherent)",
        "alert": f"sliding window, {CHUNK_WORDS} words, {CHUNK_OVERLAP} overlap",
        "min_chunk_words": MIN_CHUNK_WORDS,
    },
    "documents": documents,
}

with open(DOCS_JSON, "w", encoding="utf-8") as file:
    json.dump(payload, file, indent=2)

lengths = [len(doc["text"]) for doc in documents]

print("\n" + "=" * 62)
print("TEXT PREPARATION COMPLETE")
print("=" * 62)
print(f"  total chunks : {len(documents)}")
print(f"  chars        : min {min(lengths)}, "
      f"avg {sum(lengths) // len(lengths)}, max {max(lengths)}")
print(f"  written to   : {DOCS_JSON}")
print("\n  sample chunk:")
print(f"    {documents[0]['text'][:180]}...")
print(f"    traces to {documents[0]['metadata']['source_table']}"
      f".{documents[0]['metadata']['source_id']}")
