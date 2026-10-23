import json
import sqlite3
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from sql_schema import DB_PATH, connect

load_dotenv()

# Petit modèle rapide et peu cher : décider où envoyer une question ne demande pas GPT-4o
router_llm = ChatOpenAI(model="gpt-4o-mini", temperature=0,
                        model_kwargs={"response_format": {"type": "json_object"}})

ROUTER_LOG = []   # ce qui est envoyé au modèle de routage (affiché dans « Ce que l'IA a vu »)
AGENTS = ("rag", "sql", "qualite", "analyse", "les_deux", "hors_sujet")

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
- "qualite" : XK7-QUALITE CONTRÔLE la qualité des données de la base : doublons, valeurs manquantes, formats
  incohérents, valeurs aberrantes, liens cassés entre tables, règles métier non respectées. Exemples :
  "vérifie la qualité des données", "y a-t-il des doublons dans les clients ?", "les données sont-elles
  fiables ?", "trouve les anomalies". Une question qui demande un chiffre ou une liste précise tirée des
  données ("combien de clients à Paris ?", "combien de commandes sans date ?", "liste les clients sans
  email") va à "sql", même si elle porte sur des valeurs vides ; une question qui demande de CONTRÔLER,
  VÉRIFIER ou DÉTECTER des problèmes, sans demander un chiffre précis, va à "qualite".
  XK7-QUALITE propose aussi des CORRECTIONS (un script SQL à relire, qu'il n'exécute jamais) : une demande
  de corriger, réparer ou nettoyer les problèmes de qualité ("comment corriger ça ?" après un contrôle,
  "un script pour nettoyer les doublons") va à "qualite". Une demande d'EXÉCUTER une modification précise
  ("supprime les factures de 2009", "mets les prix à 0") va à "sql", qui la refusera.
  Une demande vague de REGARDER, OUVRIR ou EXAMINER le fichier déposé, ou de l'ANALYSER sans préciser quelle mesure, l'Excel, le CSV, le document
  déposé ou la base ("regarde l'Excel", "qu'est-ce que tu penses de mon fichier ?") va aussi à "qualite" :
  son rapport donne un aperçu complet (tables, nombre de lignes, état des données).
- "analyse" : XK7-ANALYSE fait une ANALYSE des données avec un GRAPHIQUE et une conclusion : une évolution dans
  le temps ("comment évoluent les ventes ?", "la tendance du chiffre d'affaires par mois"), une répartition en parts
  ou en pourcentages ("répartition des clients par ville", "quelle part représente chaque catégorie ?"), une
  comparaison entre groupes ("compare les ventes entre les pays"), un lien entre deux valeurs ("y a-t-il un lien
  entre le prix et la quantité ?", "est-ce que X dépend de Y ?"), ou toute demande de graphique, de courbe,
  de visualisation ou d'analyse d'une mesure ("analyse les ventes", "fais-moi un graphique des commandes").
  "analyse" seulement si la question contient un mot EXPLICITE d'analyse : évolution / évolue, tendance, au fil du
  temps, répartition / se répartissent, part, proportion, pourcentage, compare / comparaison, lien, corrélation,
  dépend, graphique, courbe, visualise, analyse. Sans un de ces mots, c'est "sql", même avec "par pays",
  "par genre", "par mois", "le mieux", "le plus", "la moyenne" : "Quel genre se vend le mieux en France ?",
  "Donne la durée moyenne des morceaux par genre", "Le chiffre d'affaires par pays" vont à "sql".
- "les_deux" : la question demande EXPLICITEMENT une explication de cours ("explique", "c'est quoi",
  "pourquoi", "comment fonctionne") ET un résultat tiré des données (ex. "explique X, puis applique-le
  à la base"). Une simple question sur les données, même difficile, va à "sql" : XK7-SQL explique déjà
  sa requête en une phrase.
- "hors_sujet" : la question ne porte ni sur les bases de données, ni sur le SQL, ni sur les données disponibles.
  Les notions internes des SGBD (isolation, niveaux d'isolation, verrous, MVCC, transactions, concurrence,
  journalisation, index, plans d'exécution) sont TOUJOURS des questions de cours ("rag"), jamais "hors_sujet",
  même si la question ne cite pas les mots "base de données".

En cas de doute :
- entre "rag" et "hors_sujet", choisis "rag" : XK7-RAG dira lui-même s'il ne trouve rien dans ses documents ;
- entre "sql" et "les_deux", choisis "sql" ;
- entre "sql" et "analyse", choisis "analyse" seulement si la question parle d'évolution, de tendance, de
  répartition, de part, de comparaison, de lien ou de graphique ; sinon "sql".

Base de données actuellement disponible pour "sql", "qualite" et "analyse" : {source}
Noms des tables : {tables}
Si l'utilisateur a déposé un fichier, "le fichier", "l'Excel" ou "le document" désignent ce fichier, et non
les ouvrages de XK7-RAG.

Questions précédentes de l'utilisateur (pour comprendre une question de suivi comme "et en 2013 ?") :
{history}

Question : {question}

Réponds uniquement avec un JSON :
{{"agent": "rag" | "sql" | "qualite" | "analyse" | "les_deux" | "hors_sujet",
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


def route(question, history=None, db_path=DB_PATH, source="la base de démonstration Chinook"):
    """Décide quel agent doit répondre. Renvoie {"agent": ..., "raison": ..., "question": ...},
    où "question" est la question réécrite pour être comprise sans l'historique.
    Confidentialité : le routeur ne voit que la question, les questions précédentes et les noms des tables,
    jamais les réponses des agents ni les données."""
    previous = [m["content"] for m in (history or []) if m.get("role") == "user"][-3:]
    prompt = ROUTER_PROMPT.format(
        tables=", ".join(table_names(db_path)),
        source=source,
        history="\n".join(f"- {q}" for q in previous) or "(aucune)",
        question=question,
    )
    ROUTER_LOG.append(prompt)
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
