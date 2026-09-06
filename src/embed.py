"""
Step 4b - Embeddings
--------------------
Encodes every chunk with a local sentence-transformers model.

MODEL CHOICE: all-MiniLM-L6-v2
    * runs locally on CPU - no API key, no per-call cost, no data
      leaving the machine, which matters for a pipeline that runs on a
      schedule and would otherwise bill per run
    * 384 dimensions, ~90 MB - small enough to download once and keep
      in the repo's cache; a 1536-dim hosted model would quadruple the
      index size for a corpus this small with no practical gain
    * strong quality-per-byte on short-passage retrieval, which is
      exactly the shape of this corpus (40-250 word chunks)
    Considered and rejected: OpenAI text-embedding-3-small (costs money
    per run and needs a key in the scheduler), bge-large (5x the size
    and slower on CPU for marginal gain at this scale).

VECTORS ARE L2-NORMALISED so that the inner product FAISS computes is
exactly cosine similarity. MiniLM is trained with cosine objectives;
using raw L2 distance on un-normalised vectors - as a naive setup does -
lets vector magnitude leak into the ranking.

The model name and revision are pinned in config.py and recorded in the
run manifest, so an upgrade can never silently change results.

    python src/embed.py
"""

import json
from datetime import datetime, timezone

import numpy as np
from sentence_transformers import SentenceTransformer

from config import (
    DOCS_JSON,
    EMBEDDINGS_JSON,
    EMBED_DIMENSIONS,
    EMBED_MODEL_NAME,
    EMBED_MODEL_REVISION,
)

BATCH_SIZE = 64

with open(DOCS_JSON, "r", encoding="utf-8") as file:
    payload = json.load(file)

documents = payload["documents"]

print(f"Loading model {EMBED_MODEL_NAME} ({EMBED_MODEL_REVISION})...")
print("(first run downloads ~90 MB; later runs use the local cache)")

model = SentenceTransformer(EMBED_MODEL_NAME, revision=EMBED_MODEL_REVISION)

texts = [document["text"] for document in documents]

print(f"Encoding {len(texts)} chunks...")

vectors = model.encode(
    texts,
    batch_size=BATCH_SIZE,
    show_progress_bar=True,
    convert_to_numpy=True,
    normalize_embeddings=True,   # cosine similarity via inner product
)

if vectors.shape[1] != EMBED_DIMENSIONS:
    raise SystemExit(
        f"Model returned {vectors.shape[1]} dimensions, config expects "
        f"{EMBED_DIMENSIONS}. Update config.EMBED_DIMENSIONS if this is "
        f"a deliberate model change."
    )

for document, vector in zip(documents, vectors):
    document["embedding"] = vector.astype("float32").tolist()

output = {
    "generated_at": datetime.now(timezone.utc).isoformat(),
    "model": EMBED_MODEL_NAME,
    "model_revision": EMBED_MODEL_REVISION,
    "dimensions": int(vectors.shape[1]),
    "normalized": True,
    "chunking": payload.get("chunking"),
    "documents": documents,
}

with open(EMBEDDINGS_JSON, "w", encoding="utf-8") as file:
    json.dump(output, file)

norms = np.linalg.norm(vectors, axis=1)

print("\n" + "=" * 62)
print("EMBEDDINGS COMPLETE")
print("=" * 62)
print(f"  chunks     : {len(documents)}")
print(f"  dimensions : {vectors.shape[1]}")
print(f"  model      : {EMBED_MODEL_NAME} @ {EMBED_MODEL_REVISION}")
print(f"  norms      : min {norms.min():.4f}, max {norms.max():.4f}  (expect 1.0)")
print(f"  written to : {EMBEDDINGS_JSON}")
