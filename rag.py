from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from vectorstore import search

load_dotenv()

llm = ChatOpenAI(model="gpt-4o", temperature=0)

PROMPT = """Tu es un assistant qui répond à des questions sur un cours de bases de données et de SQL.
Réponds uniquement à partir des extraits ci-dessous.
Si la réponse n'est pas dans les extraits, réponds : "Je ne trouve pas la réponse dans le cours."
Cite les pages utilisées sous la forme (p. X).

Extraits du cours :
{context}

Question : {question}
"""


def build_context(hits):
    """Assemble les chunks trouvés en un seul texte, avec leur numéro de page."""
    return "\n\n".join(f"[Page {h['page']}]\n{h['text']}" for h in hits)


def answer(question, k=3):
    """Cherche les passages utiles, puis demande à GPT-4o de rédiger la réponse."""
    hits = search(question, k=k)
    prompt = PROMPT.format(context=build_context(hits), question=question)
    response = llm.invoke(prompt)
    return response.content, hits


if __name__ == "__main__":
    question = "  Qui a gagné la Coupe du monde 2018 ?"
    reply, hits = answer(question)
    print(f"Question : {question}\n")
    print(reply)
    print("\nSources :", ", ".join(f"p. {h['page']}" for h in hits))