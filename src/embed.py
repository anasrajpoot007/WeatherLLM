from sentence_transformers import SentenceTransformer
import json

with open("data/processed_documents.json", "r", encoding="utf-8") as file:
    documents = json.load(file)

model = SentenceTransformer("all-MiniLM-L6-v2")

texts = [doc["text"] for doc in documents]

embeddings = model.encode(texts)

for i, doc in enumerate(documents):
    doc["embedding"] = embeddings[i].tolist()

with open("data/embeddings.json", "w", encoding="utf-8") as file:
    json.dump(documents, file, indent=4)

print("Embeddings created!")
print("Documents:", len(documents))
print("Vector size:", len(embeddings[0]))