import faiss
import json
from sentence_transformers import SentenceTransformer

# Load index
index = faiss.read_index("data/sports.index")

# Load documents
with open("data/search_documents.json", "r", encoding="utf-8") as file:
    documents = json.load(file)

# Load embedding model
model = SentenceTransformer("all-MiniLM-L6-v2")

# User query
query = input("Enter your question: ")

# Convert question into embedding
query_vector = model.encode([query])

# Search
distances, indices = index.search(query_vector, 2)

print("\nSearch Results:\n")

for i in indices[0]:
    print(documents[i]["text"])
    print("Metadata:", documents[i]["metadata"])
    print("-" * 50)