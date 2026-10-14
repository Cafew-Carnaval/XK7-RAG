from dotenv import load_dotenv
import chromadb
from langchain_openai import OpenAIEmbeddings
from loader import load_corpus

load_dotenv()

DB_PATH = "chroma_db"          # dossier où ChromaDB enregistre la base
COLLECTION = "cours_sql"       # nom de la "table" dans la base

embedder = OpenAIEmbeddings(model="text-embedding-3-small")


def get_collection():
    """Ouvre la base ChromaDB (et la crée si elle n'existe pas)."""
    client = chromadb.PersistentClient(path=DB_PATH)
    return client.get_or_create_collection(COLLECTION)


def build_index(folder="data"):
    """Découpe tous les PDF du dossier, calcule les embeddings et reconstruit la base ChromaDB."""
    chunks = load_corpus(folder)
    texts = [c["text"] for c in chunks]
    vectors = []
    for i in range(0, len(texts), 500):   # par paquets, pour les gros corpus
        vectors += embedder.embed_documents(texts[i:i + 500])   # appel à OpenAI

    client = chromadb.PersistentClient(path=DB_PATH)
    try:
        client.delete_collection(COLLECTION)    # on repart d'une base vide
    except Exception:
        pass                                    # la base n'existait pas encore
    collection = client.get_or_create_collection(COLLECTION)
    collection.add(
        ids=[f"chunk-{i}" for i in range(len(chunks))],
        documents=texts,
        embeddings=vectors,
        metadatas=[
            {"page": c["page"], "section": c["section"], "source": c["source"]} for c in chunks
        ],
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
        hits.append({
            "page": meta["page"],
            "source": meta.get("source", ""),
            "section": meta.get("section", ""),
            "text": text,
            "distance": dist,
        })
    return hits


if __name__ == "__main__":
    n = build_index("data")
    print(f"{n} chunks indexés dans ChromaDB")

    question = "C'est quoi l'intégrité référentielle ?"
    print(f"\nQuestion : {question}\n")
    for h in search(question):
        print(f"--- {h['source']}, page {h['page']} (distance {h['distance']:.3f}) ---")
        print(h["text"][:300])
        print()
