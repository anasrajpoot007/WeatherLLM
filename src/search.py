"""
Step 5b - Retrieval
-------------------
Takes a natural-language question and returns ranked, traceable chunks.

    python src/search.py                          # interactive
    python src/search.py "will it rain in Seattle"
    python src/search.py --eval                   # run the test queries

Every result prints its source table and primary key, so any answer can
be verified against weather.db directly:

    SELECT * FROM forecasts WHERE idForecast = '<source_id>';
"""

import argparse
import json
import sqlite3
import sys

import faiss
import numpy as np
from sentence_transformers import SentenceTransformer

from config import (
    DB_PATH,
    EMBED_MODEL_NAME,
    EMBED_MODEL_REVISION,
    INDEX_PATH,
    SEARCH_DOCS_JSON,
    SIMILARITY_THRESHOLD,
)

# Queries used to sanity-check retrieval quality after a rebuild.
EVAL_QUERIES = [
    "will it rain in Seattle this week",
    "where is it going to be hottest",
    "any flood warnings",
    "what should I do during a severe thunderstorm",
    "windy conditions on the coast",
    "is snow expected anywhere",
]


class Retriever:

    def __init__(self):
        self.index = faiss.read_index(INDEX_PATH)

        with open(SEARCH_DOCS_JSON, "r", encoding="utf-8") as file:
            store = json.load(file)

        self.documents = store["documents"]

        # Retrieval must use the SAME model that built the index.
        # A mismatch produces vectors in a different space and silently
        # garbage rankings, so it is checked rather than assumed.
        built_with = store.get("model")

        if built_with and built_with != EMBED_MODEL_NAME:
            sys.exit(
                f"Index was built with '{built_with}' but config says "
                f"'{EMBED_MODEL_NAME}'. Rebuild with src/embed.py."
            )

        self.model = SentenceTransformer(
            EMBED_MODEL_NAME, revision=EMBED_MODEL_REVISION
        )

    def search(self, question, top_k=5):

        vector = self.model.encode(
            [question], convert_to_numpy=True, normalize_embeddings=True
        ).astype("float32")

        scores, positions = self.index.search(vector, top_k)

        results = []

        for score, position in zip(scores[0], positions[0]):

            if position < 0:
                continue

            document = self.documents[position]

            results.append({
                "text": document["text"],
                "metadata": document["metadata"],
                "score": round(float(score), 4),
                "relevant": bool(score >= SIMILARITY_THRESHOLD),
            })

        return results


def trace(metadata):
    """Fetch the source row a chunk came from, proving traceability."""

    table = metadata.get("source_table")
    source_id = metadata.get("source_id")

    if not table or not source_id:
        return None

    key = {"forecasts": "idForecast", "alerts": "idAlert"}.get(table)

    if not key:
        return None

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    try:
        row = conn.execute(
            f"SELECT * FROM {table} WHERE {key} = ?", (source_id,)
        ).fetchone()
    finally:
        conn.close()

    return dict(row) if row else None


def show(results, verify=False):

    if not results:
        print("\nNo results.")
        return

    relevant = [item for item in results if item["relevant"]]

    if not relevant:
        best = results[0]["score"]
        print(f"\nNothing above the relevance threshold "
              f"({SIMILARITY_THRESHOLD}); best score was {best}.")
        print("Showing the closest matches anyway:\n")
        relevant = results

    for rank, item in enumerate(relevant, start=1):

        metadata = item["metadata"]

        print(f"\n[{rank}] score {item['score']}  "
              f"({metadata.get('doc_type')})")
        print(f"    {item['text'][:300]}")
        print(f"    source: {metadata.get('source_table')}"
              f"({metadata.get('source_id')})"
              f"  chunk {metadata.get('chunk_index', 0) + 1}"
              f"/{metadata.get('chunk_count', 1)}")

        if verify:
            row = trace(metadata)
            print(f"    verified in DB: {'yes' if row else 'NO - ORPHANED'}")

        print("    " + "-" * 56)


def main():

    parser = argparse.ArgumentParser(description="Search the weather corpus.")
    parser.add_argument("question", nargs="*", help="Question to ask.")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--eval", action="store_true",
                        help="Run the built-in evaluation queries.")
    parser.add_argument("--verify", action="store_true",
                        help="Join each hit back to weather.db.")
    arguments = parser.parse_args()

    retriever = Retriever()

    if arguments.eval:
        for question in EVAL_QUERIES:
            print("\n" + "=" * 62)
            print("Q:", question)
            print("=" * 62)
            show(retriever.search(question, arguments.top_k), verify=True)
        return

    if arguments.question:
        question = " ".join(arguments.question)
        print("Q:", question)
        show(retriever.search(question, arguments.top_k), arguments.verify)
        return

    while True:
        try:
            question = input("\nAsk about the weather (blank to quit): ").strip()
        except (EOFError, KeyboardInterrupt):
            break

        if not question:
            break

        show(retriever.search(question, arguments.top_k), arguments.verify)


if __name__ == "__main__":
    main()
