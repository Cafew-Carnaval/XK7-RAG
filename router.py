import json
import sqlite3
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from sql_schema import DB_PATH, connect

load_dotenv()

# Petit modèle rapide et peu cher : décider où envoyer une question ne demande pas GPT-4o
router_llm = ChatOpenAI(model="gpt-4o-mini", temperature=0,
                        model_kwargs={"response_format": {"type": "json_object"}})

AGENTS = ("rag", "sql", "les_deux", "hors_sujet")

ROUTER_PROMPT = """Tu es XK7-CORE, le chef d'orchestre d'un assistant data. Tu ne réponds jamais toi-même :
tu décides quel agent doit traiter la question de l'utilisateur.

Les agents :
- "rag" : XK7-RAG répond aux questions de COURS et de DOCUMENTATION sur les bases de données et le SQL,
  à partir d'ouvrages (cours de SQL, conception et normalisation, documentation PostgreSQL, recherche
  sur les SGBD). Exemples : expliquer une notion, une syntaxe, une différence, un fonctionnement,
  une bonne pratique. Ses sujets couvrent aussi le fonctionnement interne des SGBD : transactions,
  isolation, concurrence, verrous, index, stockage, optimisation des requêtes, architecture. Même si la question cite une table ou une requête, si elle demande d'EXPLIQUER, c'est "rag".
- "sql" : XK7-SQL interroge une BASE DE DONNÉES précise en écrivant et en exécutant une requête, pour
  obtenir des chiffres, des listes ou des classements tirés des données. Il est en lecture seule : une
  demande de modification des données va quand même à "sql", qui la refusera lui-même.
- "les_deux" : la question demande EXPLICITEMENT une explication de cours ("explique", "c'est quoi",
  "pourquoi", "comment fonctionne") ET un résultat tiré des données (ex. "explique X, puis applique-le
  à la base"). Une simple question sur les données, même difficile, va à "sql" : XK7-SQL explique déjà
  sa requête en une phrase.
- "hors_sujet" : la question ne porte ni sur les bases de données, ni sur le SQL, ni sur les données disponibles.

En cas de doute :
- entre "rag" et "hors_sujet", choisis "rag" : XK7-RAG dira lui-même s'il ne trouve rien dans ses documents ;
- entre "sql" et "les_deux", choisis "sql".

Base de données actuellement disponible pour "sql" (noms des tables seulement) :
{tables}

Questions précédentes de l'utilisateur (pour comprendre une question de suivi comme "et en 2013 ?") :
{history}

Question : {question}

Réponds uniquement avec un JSON :
{{"agent": "rag" | "sql" | "les_deux" | "hors_sujet",
  "raison": "une phrase courte",
  "question": "la question réécrite pour être compréhensible seule, sans l'historique"}}
Pour "question" : si la question est une suite ("et en 2013 ?", "et ceux qui ont dépensé moins ?"), complète-la
avec ce que l'historique apporte. Sinon, recopie-la telle quelle. Ne réponds jamais à la question."""


def table_names(db_path=DB_PATH):
    """Noms des tables de la base : la seule information sur les données que voit le routeur."""
    conn = connect(db_path)
    try:
        return [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    finally:
        conn.close()


def route(question, history=None, db_path=DB_PATH):
    """Décide quel agent doit répondre. Renvoie {"agent": ..., "raison": ..., "question": ...},
    où "question" est la question réécrite pour être comprise sans l'historique.
    Confidentialité : le routeur ne voit que la question, les questions précédentes et les noms des tables,
    jamais les réponses des agents ni les données."""
    previous = [m["content"] for m in (history or []) if m.get("role") == "user"][-3:]
    prompt = ROUTER_PROMPT.format(
        tables=", ".join(table_names(db_path)),
        history="\n".join(f"- {q}" for q in previous) or "(aucune)",
        question=question,
    )
    raw = router_llm.invoke(prompt).content
    try:
        decision = json.loads(raw)
    except ValueError:
        decision = {}
    if decision.get("agent") not in AGENTS:
        # En cas de réponse illisible, on choisit le RAG : au pire, il dira qu'il ne trouve pas
        decision = {"agent": "rag", "raison": "décision illisible, envoi par défaut au RAG"}
    # Question autonome : on la garde seulement si elle reste raisonnable (le modèle n'a pas répondu à la place)
    rewritten = str(decision.get("question") or "").strip()
    limit = len(question) + sum(len(q) for q in previous) + 50
    decision["question"] = rewritten if rewritten and len(rewritten) <= limit else question
    return decision


if __name__ == "__main__":
    for q in ["C'est quoi une clé étrangère ?",
              "Combien de clients habitent au Canada ?",
              "Explique le GROUP BY, puis donne le chiffre d'affaires par pays.",
              "Quel temps fait-il à Paris ?"]:
        d = route(q)
        print(f"{d['agent']:<12}{q}\n{'':<12}{d['raison']}")
