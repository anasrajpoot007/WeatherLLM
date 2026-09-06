import faiss
import json
import numpy as np

with open("data/embeddings.json", "r", encoding="utf-8") as file:
    documents = json.load(file)

vectors = np.array(
    [doc["embedding"] for doc in documents],
    dtype="float32"
)

index = faiss.IndexFlatL2(vectors.shape[1])
index.add(vectors)

faiss.write_index(index, "data/sports.index")

with open("data/search_documents.json", "w", encoding="utf-8") as file:
    json.dump(documents, file, indent=4)

print("Vector index created!")
print("Vectors:", index.ntotal)
print("Vector dimension:", vectors.shape[1])