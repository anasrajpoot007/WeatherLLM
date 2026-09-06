import faiss
import json
from sentence_transformers import SentenceTransformer

# Load index
index = faiss.read_index("data/sports.index")

# Load documents
with open("data/search_documents.json", "r", encoding="utf-8") as file:
    documents = json.load(file)

# Load model
model = SentenceTransformer("all-MiniLM-L6-v2")

# User query
query = input("Enter your question: ")

# Convert question to vector
query_vector = model.encode([query])

# Search top 2
distances, indices = index.search(query_vector, 2)

print("\nSearch Results:\n")

# Relevance threshold
threshold = 1.0

found = False

for distance, i in zip(distances[0], indices[0]):

    if distance <= threshold:
        print(documents[i]["text"])
        print("Metadata:", documents[i]["metadata"])
        print("-" * 50)
        found = True

if not found:
    print("No relevant result found.")