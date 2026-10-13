from dotenv import load_dotenv
import chromadb
from langchain_openai import OpenAIEmbeddings
from loader import load_pdf, split_pages

load_dotenv()

DB_PATH = "chroma_db"          # dossier où ChromaDB enregistre la base
COLLECTION = "cours_sql"       # nom de la "table" dans la base

embedder = OpenAIEmbeddings(model="text-embedding-3-small")


def get_collection():
    """Ouvre la base ChromaDB (et la crée si elle n'existe pas)."""
    client = chromadb.PersistentClient(path=DB_PATH)
    return client.get_or_create_collection(COLLECTION)


def build_index(pdf_path):
    """Lit le PDF, le découpe, calcule les embeddings et range tout dans ChromaDB."""
    chunks = split_pages(load_pdf(pdf_path))
    texts = [c["text"] for c in chunks]
    vectors = embedder.embed_documents(texts)   # appel à OpenAI

    collection = get_collection()
    collection.upsert(
        ids=[f"chunk-{i}" for i in range(len(chunks))],
        documents=texts,
        embeddings=vectors,
        metadatas=[{"page": c["page"], "source": pdf_path} for c in chunks],
    )
    return len(chunks)


def search(question, k=3):
    """Renvoie les k chunks les plus proches de la question."""
    collection = get_collection()
    vector = embedder.embed_query(question)
    results = collection.query(query_embeddings=[vector], n_results=k)

    hits = []
    for text, meta, dist in zip(
        results["documents"][0], results["metadatas"][0], results["distances"][0]
    ):
        hits.append({"page": meta["page"], "text": text, "distance": dist})
    return hits


if __name__ == "__main__":
    n = build_index("data/test.pdf")
    print(f"{n} chunks indexés dans ChromaDB")

    question = "C'est quoi l'intégrité référentielle ?"
    print(f"\nQuestion : {question}\n")
    for h in search(question):
        print(f"--- page {h['page']} (distance {h['distance']:.3f}) ---")
        print(h["text"][:300])
        print()