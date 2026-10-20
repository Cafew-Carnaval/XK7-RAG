import json
import unicodedata
from typing import TypedDict, Optional
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from langgraph.graph import StateGraph, START, END
from router import route
from sql_schema import DB_PATH

load_dotenv()

split_llm = ChatOpenAI(model="gpt-4o-mini", temperature=0,
                       model_kwargs={"response_format": {"type": "json_object"}})

OFF_TOPIC_MESSAGE = ("Je ne peux pas répondre : XK7 est spécialisé dans les bases de données et le SQL. "
                     "Tu peux me poser une question de cours, par exemple « C'est quoi une jointure externe ? », "
                     "ou une question sur les données, par exemple « Quels sont les 5 clients qui ont le plus dépensé ? ».")

SPLIT_PROMPT = """La question suivante contient deux demandes : une explication de cours sur les bases de données
ou le SQL, et une demande de résultat tiré d'une base de données.
Sépare-les en deux questions courtes et autonomes, en reprenant les mots de l'utilisateur.
Tu ne dois PAS répondre aux questions ni ajouter d'explication : seulement les recopier, séparées.

Exemple :
Question : Explique le GROUP BY, puis donne le chiffre d'affaires par pays.
JSON : {{"cours": "Explique le GROUP BY.", "data": "Donne le chiffre d'affaires par pays."}}

Question : {question}
JSON :"""


def plain(text):
    """Clé simplifiée : minuscules, sans accents ("Données" -> "donnees")."""
    return unicodedata.normalize("NFKD", str(text)).encode("ascii", "ignore").decode().lower().strip()


class State(TypedDict, total=False):
    """Ce qui circule entre les étapes du graphe."""
    question: str            # question de l'utilisateur
    history: list            # messages précédents
    db_path: str             # base interrogée par XK7-SQL
    level: str               # niveau des réponses de XK7-RAG
    k: int                   # nombre de passages pour XK7-RAG
    agent: str               # décision du routeur : rag, sql, les_deux, hors_sujet
    reason: str              # raison donnée par le routeur
    rag_question: str        # partie "cours" de la question
    sql_question: str        # partie "données" de la question
    rag: Optional[dict]      # réponse de XK7-RAG : {"reply", "hits"}
    sql: Optional[dict]      # résultat de XK7-SQL (voir sql_agent.ask)
    message: str             # message final quand aucun agent ne répond
    split_ok: bool           # le découpage d'une question mixte a-t-il réussi ?
    standalone: str          # question réécrite pour être comprise sans l'historique


# --- Les étapes (nœuds) du graphe -------------------------------------------------------

def route_node(state):
    """XK7-CORE choisit l'agent."""
    decision = route(state["question"], state.get("history"), state.get("db_path", DB_PATH))
    standalone = decision["question"]   # question de suivi complétée grâce à l'historique
    return {"agent": decision["agent"], "reason": decision.get("raison", ""), "standalone": standalone,
            "rag_question": standalone, "sql_question": standalone}


def split_node(state):
    """Pour une question mixte : sépare la partie cours et la partie données.
    Le modèle ne voit que la question, jamais de données."""
    question = state.get("standalone") or state["question"]
    raw = split_llm.invoke(SPLIT_PROMPT.format(question=question)).content
    try:
        parts = {plain(k): str(v).strip() for k, v in json.loads(raw).items()}
    except (ValueError, AttributeError):
        parts = {}
    # On accepte les variantes de clés que le modèle invente parfois ("données", "explication"...)
    rag_q = next((v for k, v in parts.items() if k.startswith(("cours", "expl"))), "")
    sql_q = next((v for k, v in parts.items() if k.startswith(("data", "donnee"))), "")
    # Garde-fou : une partie plus longue que la question entière, c'est que le modèle a répondu
    # au lieu de découper. On ne garde jamais un texte qu'il aurait inventé : la partie fautive
    # est remplacée par la question entière (signalé dans l'app).
    rag_ok = bool(rag_q) and len(rag_q) <= len(question)
    sql_ok = bool(sql_q) and len(sql_q) <= len(question)
    return {"rag_question": rag_q if rag_ok else question,
            "sql_question": sql_q if sql_ok else question,
            "split_ok": rag_ok and sql_ok}


def rag_node(state):
    """XK7-RAG répond à la partie cours."""
    from rag import answer   # import ici : charger le RAG (ChromaDB) seulement s'il sert
    history = [m for m in state.get("history", []) if m.get("agent") in ("rag", "les_deux")]
    reply, hits = answer(state["rag_question"], k=state.get("k", 3), history=history,
                         level=state.get("level", "Intermédiaire"))
    return {"rag": {"reply": reply, "hits": hits}}


def sql_node(state):
    """XK7-SQL répond à la partie données. Son résultat n'est renvoyé à aucun modèle."""
    from sql_agent import ask
    return {"sql": ask(state["sql_question"], state.get("db_path", DB_PATH))}


def off_topic_node(state):
    return {"message": OFF_TOPIC_MESSAGE}


# --- Les chemins entre les étapes -------------------------------------------------------

def after_route(state):
    return {"rag": "rag", "sql": "sql", "les_deux": "split", "hors_sujet": "hors_sujet"}[state["agent"]]


def after_rag(state):
    return "sql" if state["agent"] == "les_deux" else END


def build_graph():
    graph = StateGraph(State)
    graph.add_node("route", route_node)
    graph.add_node("split", split_node)
    graph.add_node("rag", rag_node)
    graph.add_node("sql", sql_node)
    graph.add_node("hors_sujet", off_topic_node)

    graph.add_edge(START, "route")
    graph.add_conditional_edges("route", after_route, ["rag", "sql", "split", "hors_sujet"])
    graph.add_edge("split", "rag")                      # question mixte : d'abord le cours...
    graph.add_conditional_edges("rag", after_rag, ["sql", END])   # ... puis les données
    graph.add_edge("sql", END)
    graph.add_edge("hors_sujet", END)
    return graph.compile()


core = build_graph()


def run(question, history=None, db_path=DB_PATH, level="Intermédiaire", k=3):
    """Point d'entrée de XK7-CORE : une question, et le graphe fait le reste."""
    return core.invoke({"question": question, "history": history or [], "db_path": db_path,
                        "level": level, "k": k})


if __name__ == "__main__":
    for q in ["Combien de clients habitent au Canada ?",
              "C'est quoi une jointure externe ? Montre-moi les employés qui n'ont aucun client.",
              "Donne-moi une recette de crêpes."]:
        result = run(q)
        print(f"\n=== {q}\n-> {result['agent']} ({result['reason']})")
        if result.get("agent") == "les_deux":
            print(f"   partie cours   : {result['rag_question']}")
            print(f"   partie données : {result['sql_question']}")
        if result.get("rag"):
            print("\n[XK7-RAG]\n" + result["rag"]["reply"][:600])
        if result.get("sql"):
            s = result["sql"]
            print("\n[XK7-SQL]")
            if s["ok"]:
                print(s["sql"])
                print(s["columns"], s["rows"][:5], f"({len(s['rows'])} lignes)")
            else:
                print(s["message"])
        if result.get("message"):
            print(result["message"])
