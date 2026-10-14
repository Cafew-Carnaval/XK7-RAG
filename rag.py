import re
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from vectorstore import search, get_collection

load_dotenv()

llm = ChatOpenAI(model="gpt-4o", temperature=0)

PROMPT = """Tu es un assistant pédagogique qui répond à des questions sur un cours de bases de données et de SQL.

Règles (elles passent avant tout le reste) :
- Réponds uniquement à partir des extraits ci-dessous, sans ajouter de connaissances extérieures.
- N'écris jamais de code SQL qui n'apparaît pas dans les extraits. Si tu montres du code, recopie-le tel quel depuis les extraits.
- Cite les pages utilisées sous la forme (p. X), uniquement pour des informations qui figurent réellement sur ces pages.
- Si les extraits ne contiennent qu'une partie de la réponse, donne cette partie et précise ce qui manque.
- Si les extraits ne contiennent rien d'utile pour répondre, réponds uniquement : "Je ne trouve pas la réponse dans le cours."

Niveau de la personne et façon de répondre :
{level_instruction}

Extraits du cours :
{context}

Question : {question}
"""

# Consigne ajoutée au prompt selon le niveau choisi : même contenu, façon d'expliquer différente
LEVELS = {
    "Débutant": (
        "La personne n'a pas de formation en bases de données. "
        "Réponds complètement à la question, mais en vulgarisant : mots de tous les jours, phrases courtes, "
        "explique chaque terme technique la première fois que tu l'emploies, avance étape par étape, "
        "et appuie-toi sur une comparaison de la vie courante ou un exemple concret. "
        "Si tu montres du code, explique en langage simple ce que fait chaque partie."
    ),
    "Intermédiaire": (
        "La personne a un niveau bac+1 à bac+5 en informatique ou en data. "
        "Utilise le vocabulaire technique standard, en expliquant brièvement les notions moins courantes. "
        "Structure ta réponse : définition, fonctionnement, syntaxe, puis exemple."
    ),
    "Expert": (
        "La personne a un niveau bac+6 et plus (doctorat, spécialiste) et maîtrise le domaine. "
        "Va droit au but avec le vocabulaire technique exact, sans réexpliquer les notions de base. "
        "Fais une réponse dense et précise, et mets en avant les subtilités, cas limites et implications "
        "présents dans les extraits."
    ),
}

REWRITE_PROMPT = """Voici le début d'une conversation sur les bases de données, puis une nouvelle question de l'utilisateur.
Réécris la nouvelle question pour qu'elle soit compréhensible seule, sans l'historique.
Ne réponds pas à la question. Si elle est déjà compréhensible seule, renvoie-la telle quelle.

Historique :
{history}

Nouvelle question : {question}

Question réécrite :"""


_ALL_CHUNKS = None


def all_chunks():
    """Charge une fois tous les chunks de la base, dans l'ordre du document."""
    global _ALL_CHUNKS
    if _ALL_CHUNKS is None:
        data = get_collection().get(include=["documents", "metadatas"])
        chunks = [
            {"id": int(i.split("-")[1]), "page": m["page"], "section": m.get("section", ""), "text": d}
            for i, d, m in zip(data["ids"], data["documents"], data["metadatas"])
        ]
        _ALL_CHUNKS = sorted(chunks, key=lambda c: c["id"])
    return _ALL_CHUNKS


def expand_sections(hits, max_chars=8000, max_section_chars=6000):
    """Complète chaque passage trouvé avec le reste de sa section, comme un lecteur qui lit
    toute la section après avoir trouvé le bon paragraphe."""
    chunks = all_chunks()
    result, seen, total = [], set(), 0
    for h in hits:
        label = h.get("section", "")
        same = [c for c in chunks if label and c["section"] == label]
        subtree = [c for c in chunks if label and (c["section"] == label or c["section"].startswith(label + " > "))]
        # Section avec ses sous-sections si elle reste raisonnable, sinon la section seule
        if subtree and sum(len(c["text"]) for c in subtree) <= max_section_chars:
            group = subtree
        else:
            group = same or [h]
        for c in group:
            if c["text"] in seen:
                continue
            if result and total + len(c["text"]) > max_chars:
                return result
            seen.add(c["text"])
            total += len(c["text"])
            result.append({**c, "distance": h["distance"]})
    return result


def build_context(hits):
    """Assemble les chunks trouvés en un seul texte, avec leur numéro de page."""
    return "\n\n".join(f"[Page {h['page']}]\n{h['text']}" for h in hits)


def rewrite_question(question, history):
    """Rend une question de suivi compréhensible seule ("et pour rollback ?")."""
    if not history:
        return question
    lines = []
    for msg in history[-6:]:  # les 3 derniers échanges
        role = "Utilisateur" if msg["role"] == "user" else "Assistant"
        lines.append(f"{role} : {msg['content'][:500]}")
    prompt = REWRITE_PROMPT.format(history="\n".join(lines), question=question)
    return llm.invoke(prompt).content.strip()


def cited_pages(reply):
    """Renvoie l'ensemble des numéros de page cités dans la réponse, ex. (p. 81) ou (p. 97, 98)."""
    pages = set()
    for group in re.findall(r"\(p\.\s*([\d\s,et\-–p.]+)\)", reply):
        pages.update(int(n) for n in re.findall(r"\d+", group))
    return pages


def answer(question, k=3, history=None, level="Intermédiaire"):
    """Cherche les passages utiles, puis demande à GPT-4o de rédiger la réponse au niveau choisi."""
    question = rewrite_question(question, history)
    hits = expand_sections(search(question, k=k))
    prompt = PROMPT.format(
        context=build_context(hits),
        question=question,
        level_instruction=LEVELS.get(level, LEVELS["Intermédiaire"]),
    )
    response = llm.invoke(prompt)
    return response.content, hits


if __name__ == "__main__":
    question = "C'est quoi l'intégrité référentielle ?"
    reply, hits = answer(question)
    print(f"Question : {question}\n")
    print(reply)
    print("\nPages citées :", sorted(cited_pages(reply)))
