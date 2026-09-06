"""
Step 5a - Build the vector index
--------------------------------
Writes a FAISS index plus the aligned document store.

INDEX CHOICE: IndexFlatIP (exact inner product)
    Vectors are L2-normalised in embed.py, so inner product == cosine
    similarity, in the range -1..1 where higher is better. This is the
    metric MiniLM was trained for.

    Flat = exhaustive search. For a corpus of a few thousand chunks that
    is microseconds per query and returns the exact nearest neighbours.
    An approximate index (IVF, HNSW) only starts paying off in the
    hundreds of thousands of vectors, and would cost recall plus a
    training step here for no measurable speed gain.

WHERE THE VECTORS LIVE
    A local FAISS file, not a hosted vector database. The corpus is
    small, rebuilds in seconds from the relational tables, and adding a
    network service would mean another process to run, secure and pay
    for. sqlite-vec / pgvector were the other candidates: both keep
    vectors next to the rows, which is appealing, but SQLite here is a
    file the pipeline rewrites freely and FAISS keeps the retrieval
    layer swappable without touching the schema. Documented in the
    README as a decision to revisit if the corpus grows.

    python src/vector_search.py
"""

import json
from datetime import datetime, timezone

import faiss
import numpy as np

from config import (
    EMBEDDINGS_JSON,
    INDEX_PATH,
    MANIFEST_PATH,
    SEARCH_DOCS_JSON,
)

with open(EMBEDDINGS_JSON, "r", encoding="utf-8") as file:
    payload = json.load(file)

documents = payload["documents"]

vectors = np.array(
    [document["embedding"] for document in documents],
    dtype="float32",
)

# Guard against an un-normalised corpus sneaking in: inner product only
# equals cosine similarity when every vector has unit length.
norms = np.linalg.norm(vectors, axis=1)

if not np.allclose(norms, 1.0, atol=1e-3):
    print("Vectors are not unit length - normalising before indexing.")
    faiss.normalize_L2(vectors)

index = faiss.IndexFlatIP(vectors.shape[1])
index.add(vectors)

faiss.write_index(index, INDEX_PATH)

# The document store is the index's row-order companion: FAISS returns
# positions, and position i must map to documents[i]. Embeddings are
# stripped - they live in the index, and carrying them twice triples
# the file for nothing.
store = {
    "generated_at": datetime.now(timezone.utc).isoformat(),
    "model": payload.get("model"),
    "model_revision": payload.get("model_revision"),
    "dimensions": payload.get("dimensions"),
    "documents": [
        {"text": document["text"], "metadata": document["metadata"]}
        for document in documents
    ],
}

with open(SEARCH_DOCS_JSON, "w", encoding="utf-8") as file:
    json.dump(store, file)

# Run manifest: which model and how much data produced this index.
# The brief asks for this - results shift silently when a dependency
# upgrades, and without a manifest you cannot tell which run is which.
manifest = {
    "built_at": datetime.now(timezone.utc).isoformat(),
    "embedding_model": payload.get("model"),
    "embedding_revision": payload.get("model_revision"),
    "dimensions": int(vectors.shape[1]),
    "vectors": int(index.ntotal),
    "index_type": "IndexFlatIP",
    "metric": "cosine (inner product on normalised vectors)",
    "chunking": payload.get("chunking"),
    "doc_types": {},
}

for document in documents:
    doc_type = document["metadata"].get("doc_type", "unknown")
    manifest["doc_types"][doc_type] = manifest["doc_types"].get(doc_type, 0) + 1

with open(MANIFEST_PATH, "w", encoding="utf-8") as file:
    json.dump(manifest, file, indent=2)

print("=" * 62)
print("VECTOR INDEX BUILT")
print("=" * 62)
print(f"  vectors    : {index.ntotal}")
print(f"  dimensions : {vectors.shape[1]}")
print(f"  index type : IndexFlatIP (exact cosine)")
print(f"  by type    : {manifest['doc_types']}")
print(f"  written to : {INDEX_PATH}")
print(f"  manifest   : {MANIFEST_PATH}")
