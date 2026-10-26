import re
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from vectorstore import search, get_collection

load_dotenv()

llm = ChatOpenAI(model="gpt-4o", temperature=0)               # rédige les réponses
small_llm = ChatOpenAI(model="gpt-4o-mini", temperature=0)    # petites tâches : réécrire, traduire

REFUS = "Je ne trouve pas la réponse dans les documents"

PROMPT = """Tu es un assistant pédagogique expert en bases de données et en SQL.
Tu réponds en français, même si les extraits sont en anglais.

Règles (elles passent avant tout le reste) :
- Réponds uniquement à partir des extraits ci-dessous, sans ajouter de connaissances extérieures.
- Les comparaisons de la vie courante et les petits exemples d'illustration demandés par le niveau sont autorisés, à condition de ne rien affirmer de technique qui ne soit pas dans les extraits. Une comparaison ne doit jamais contredire les extraits : vérifie qu'elle reste vraie dans les cas que les extraits décrivent (par exemple, quand les extraits disent qu'un mécanisme ne coûte rien dans un cas, la comparaison ne doit pas montrer un coût dans ce cas).
- N'écris jamais de code SQL qui n'apparaît pas dans les extraits. Si tu montres du code, recopie-le tel quel depuis les extraits.
- Cite tes sources sous la forme (Titre du document, p. X), en reprenant exactement le titre indiqué entre crochets avant chaque extrait, et uniquement pour des informations qui figurent réellement sur ces pages.
- Si les extraits ne contiennent qu'une partie de la réponse, donne cette partie et précise ce qui manque.
- Si les extraits ne contiennent rien d'utile pour répondre, ignore le format demandé par le niveau et réponds
  uniquement : "Je ne trouve pas la réponse dans les documents." N'écris rien d'autre, ni avant ni après.

Niveau de la personne et façon de répondre :
{level_instruction}

Extraits des documents :
{context}

Question : {question}
"""

# Consigne ajoutée au prompt selon le niveau choisi : même contenu, façon d'expliquer différente
LEVELS = {
    "Débutant": (
        "La personne n'a aucune formation en bases de données. Respecte strictement ce format :\n"
        "1. Commence par une image de la vie courante qui fait comprendre l'idée (une bibliothèque, un fichier "
        "de contacts, une liste de courses...), en 2 ou 3 phrases.\n"
        "2. Explique ensuite la notion avec des mots de tous les jours, en phrases courtes. "
        "Chaque mot technique (clé primaire, attribut, relation...) doit être expliqué entre parenthèses "
        "la première fois qu'il apparaît.\n"
        "3. Termine par un petit exemple concret, avec des données simples (des élèves, des livres...).\n"
        "Pas de notation mathématique. Pas de code, sauf une requête très courte expliquée ligne par ligne."
    ),
    "Intermédiaire": (
        "La personne a un niveau bac+1 à bac+5 en informatique ou en data. Respecte ce format :\n"
        "1. Une définition précise avec le vocabulaire technique standard.\n"
        "2. Le fonctionnement ou les règles, sous forme de liste si c'est plus clair.\n"
        "3. La syntaxe SQL ou un exemple tiré des extraits, s'il y en a.\n"
        "Explique brièvement les notions moins courantes."
    ),
    "Expert": (
        "La personne a un niveau bac+6 et plus (doctorat, spécialiste). Respecte ce format :\n"
        "1. Une définition formelle et concise, avec la notation du domaine quand elle s'applique "
        "(par exemple X → Y pour une dépendance fonctionnelle).\n"
        "2. Les subtilités, cas limites, pièges et implications (performance, cohérence, conception) "
        "présents dans les extraits.\n"
        "3. Les liens avec les notions voisines présentes dans les extraits.\n"
        "Ne réexplique pas les notions de base. Style dense, sans phrases d'introduction."
    ),
}

# Quand la bibliothèque ne contient rien : une explication générale, courte, clairement étiquetée « non sourcée »
GENERAL_PROMPT = """Tu es un assistant pédagogique en bases de données et en SQL. La bibliothèque de documents ne
contient pas la réponse à la question suivante. Donne une explication générale courte (5 à 8 lignes), en français,
avec tes connaissances. Ne cite aucune source, aucun titre de livre, aucune page. Si la question ne porte pas sur les
données, les bases de données, le SQL ou l'analyse de données, réponds uniquement : HORS_SUJET

Niveau de la personne :
{level_instruction}

Question : {question}"""
GENERAL_NOTE = ("Voici une explication générale, **non sourcée** : elle ne vient pas de la bibliothèque, "
                "vérifie-la avant de t'y fier.")

REWRITE_PROMPT = """Voici le début d'une conversation sur les bases de données, puis une nouvelle question de l'utilisateur.
Réécris la nouvelle question pour qu'elle soit compréhensible seule, sans l'historique.
Ne réponds pas à la question. Si elle est déjà compréhensible seule, renvoie-la telle quelle.

Historique :
{history}

Nouvelle question : {question}

Question réécrite :"""

TRANSLATE_PROMPT = """Traduis cette question en anglais, en gardant les termes techniques exacts.
Ne réponds pas à la question. Renvoie uniquement la traduction.

Question : {question}"""


_ALL_CHUNKS = None


def all_chunks():
    """Charge une fois tous les chunks de la base, dans l'ordre du document."""
    global _ALL_CHUNKS
    if _ALL_CHUNKS is None:
        data = get_collection().get(include=["documents", "metadatas"])
        chunks = [
            {
                "id": int(i.split("-")[1]),
                "page": m["page"],
                "source": m.get("source", ""),
                "section": m.get("section", ""),
                "text": d,
            }
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
        label, source = h.get("section", ""), h.get("source", "")
        doc = [c for c in chunks if c["source"] == source]
        same = [c for c in doc if label and c["section"] == label]
        subtree = [c for c in doc if label and (c["section"] == label or c["section"].startswith(label + " > "))]
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
    return "\n\n".join(f"[{h['source']}, page {h['page']}]\n{h['text']}" for h in hits)


def rewrite_question(question, history):
    """Rend une question de suivi compréhensible seule ("et pour rollback ?")."""
    if not history:
        return question
    lines = []
    for msg in history[-6:]:  # les 3 derniers échanges
        role = "Utilisateur" if msg["role"] == "user" else "Assistant"
        lines.append(f"{role} : {msg['content'][:500]}")
    prompt = REWRITE_PROMPT.format(history="\n".join(lines), question=question)
    return small_llm.invoke(prompt).content.strip()


def retrieve(question, k=3):
    """Cherche avec la question en français ET traduite en anglais (une partie des livres est en anglais),
    puis alterne les résultats des deux recherches pour garder des passages des deux langues."""
    english = small_llm.invoke(TRANSLATE_PROMPT.format(question=question)).content.strip()
    french_hits, english_hits = search(question, k=k), search(english, k=k)
    merged, seen = [], set()
    for pair in zip(french_hits, english_hits):
        for h in pair:
            if h["text"] not in seen:
                seen.add(h["text"])
                merged.append(h)
    return merged[:k]


def cited_sources(reply):
    """Renvoie les couples (titre, page) cités dans la réponse, ex. (Database Design, p. 74)."""
    cited = set()
    for title, pages in re.findall(r"\(([^()]+?),\s*p\.\s*([\d\s,et\-–p.]+)\)", reply):
        for n in re.findall(r"\d+", pages):
            cited.add((title.strip(), int(n)))
    return cited


def keep_cited(hits, reply):
    """Ne garde que les passages réellement cités dans la réponse."""
    cited = cited_sources(reply)
    return [h for h in hits if (h["source"], h["page"]) in cited]


def answer(question, k=3, history=None, level="Intermédiaire"):
    """Cherche les passages utiles, puis demande à GPT-4o de rédiger la réponse au niveau choisi."""
    question = rewrite_question(question, history)
    hits = expand_sections(retrieve(question, k=k))
    prompt = PROMPT.format(
        context=build_context(hits),
        question=question,
        level_instruction=LEVELS.get(level, LEVELS["Intermédiaire"]),
    )
    reply = llm.invoke(prompt).content.strip()
    if REFUS in reply:
        # Rien dans la bibliothèque : plutôt qu'un refus sec, une explication générale clairement étiquetée.
        # Le refus reste en tête (l'app n'affiche alors aucune source, l'évaluation le reconnaît).
        leftover = reply.replace(REFUS + ".", "").replace(REFUS, "").strip()
        if len(leftover) < 150:             # le modèle a bien refusé seul : on demande l'explication générale
            leftover = llm.invoke(GENERAL_PROMPT.format(
                question=question, level_instruction=LEVELS.get(level, LEVELS["Intermédiaire"]))).content.strip()
        reply = f"{REFUS}." if leftover.startswith("HORS_SUJET") else f"{REFUS}. {GENERAL_NOTE}\n\n{leftover}"
    return reply, hits


if __name__ == "__main__":
    question = "C'est quoi l'intégrité référentielle ?"
    reply, hits = answer(question)
    print(f"Question : {question}\n")
    print(reply)
    print("\nSources citées :", sorted(cited_sources(reply)))
