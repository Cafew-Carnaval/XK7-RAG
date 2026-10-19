import os
import pandas as pd
import streamlit as st
from dotenv import load_dotenv
from rag import answer, keep_cited, LEVELS, REFUS
from sql_agent import ask, MAX_ROWS
from sql_schema import DB_PATH
from data_upload import to_sqlite, delete_file, UploadError, MAX_SIZE_MB

load_dotenv()

MAX_QUESTIONS = 20  # nombre maximum de questions par visite
AVATARS = {
    "user": ":material/person:",
    "assistant": ":material/database:",
}
AGENTS = {
    "rag": "XK7-RAG · Documentation",
    "sql": "XK7-SQL · Données",
}

st.set_page_config(page_title="XK7", page_icon=":material/database:")

# --- Mot de passe : demandé seulement s'il est défini (dans .env ou dans les secrets en ligne)
password = os.getenv("APP_PASSWORD")
if password and not st.session_state.get("auth"):
    st.title("XK7")
    entered = st.text_input("Mot de passe", type="password")
    if entered == password:
        st.session_state["auth"] = True
        st.rerun()
    elif entered:
        st.error("Mot de passe incorrect.")
    st.stop()

# --- Mémoire de la session
st.session_state.setdefault("messages", [])
st.session_state.setdefault("nb_questions", 0)
st.session_state.setdefault("upload", None)   # {"name", "size", "path"} du fichier déposé


def show_sources(sources):
    """Affiche les passages cités, regroupés par document et par page."""
    if not sources:
        return
    pages = {}
    for h in sources:
        key = (h.get("source", ""), h["page"])
        text = h["text"].split("\n", 1)[1] if "\n" in h["text"] else ""   # retire la ligne de titre
        if key not in pages:
            section = " > ".join(h.get("section", "").split(" > ")[-2:])
            pages[key] = {"section": section, "texts": []}
        pages[key]["texts"].append(text)
    with st.expander(f"Sources ({len(pages)})"):
        for (source, page), info in pages.items():
            excerpt = " ".join(info["texts"])
            st.markdown(f"**{source}** · {info['section']} · page {page}")
            st.caption(excerpt[:600] + ("…" if len(excerpt) > 600 else ""))


def show_sql_result(msg, key):
    """Affiche le résultat de XK7-SQL : tableau, téléchargement et requête utilisée."""
    if not msg.get("columns"):
        return
    df = pd.DataFrame(msg["rows"], columns=msg["columns"])
    st.dataframe(df, hide_index=True, width="stretch")
    if msg.get("truncated"):
        st.caption(f"Résultat limité aux {MAX_ROWS} premières lignes.")
    st.download_button("Télécharger en CSV", df.to_csv(index=False).encode("utf-8"),
                       file_name="resultat.csv", mime="text/csv", key=f"dl_{key}",
                       icon=":material/download:")
    with st.expander("Requête SQL utilisée"):
        st.code(msg["sql"], language="sql")


def reset_upload():
    """Supprime le fichier temporaire de l'utilisateur."""
    if st.session_state["upload"]:
        delete_file(st.session_state["upload"]["path"])
        st.session_state["upload"] = None


# --- Barre latérale
with st.sidebar:
    st.title("XK7")
    st.caption("Assistant IA pour les données et la documentation")

    if st.button("Nouvelle conversation", icon=":material/add:", width="stretch"):
        st.session_state["messages"] = []
        st.rerun()

    st.divider()
    agent = st.radio(
        "Agent", list(AGENTS), format_func=AGENTS.get,
        captions=["Questions de cours et de documentation", "Questions sur une base de données"],
    )

    st.divider()
    st.subheader("Réglages")
    db_path, db_label = DB_PATH, "Chinook (démonstration)"
    if agent == "rag":
        level = st.radio(
            "Niveau des réponses",
            list(LEVELS),
            index=1,
            captions=["Grand public", "Bac+1 à bac+5", "Bac+6 et plus"],
            help="Adapte la façon d'expliquer : même réponse, plus ou moins vulgarisée. "
                 "Les informations viennent toujours des documents.",
        )
        k = st.slider(
            "Nombre de passages consultés",
            1, 10, 3,
            help="Nombre d'extraits que l'assistant recherche avant de répondre.",
        )
        st.caption(
            "Peu de passages (1 à 3) : réponse plus ciblée. "
            "Plus de passages (5 à 10) : utile si la réponse est répartie sur plusieurs parties des documents. "
            "Si l'assistant ne trouve pas, augmente ce nombre ou reformule ta question."
        )
    else:
        source = st.radio("Base interrogée", ["Base de démonstration", "Mon fichier"],
                          captions=["Chinook : une boutique de musique en ligne", "CSV, Excel ou SQLite"])
        if source == "Mon fichier":
            uploaded = st.file_uploader(f"Dépose ton fichier (max {MAX_SIZE_MB} Mo)",
                                        type=["csv", "xlsx", "db", "sqlite", "sqlite3"])
            if uploaded is None:
                reset_upload()
                db_path = None
            else:
                current = st.session_state["upload"]
                if not current or (current["name"], current["size"]) != (uploaded.name, uploaded.size):
                    reset_upload()
                    try:
                        path = to_sqlite(uploaded.name, uploaded.getvalue())
                        st.session_state["upload"] = {"name": uploaded.name, "size": uploaded.size, "path": path}
                    except UploadError as e:
                        st.error(str(e))
                if st.session_state["upload"]:
                    db_path, db_label = st.session_state["upload"]["path"], uploaded.name
                else:
                    db_path = None
        st.caption(
            "Mode confidentiel : seule la structure de la base (noms des tables et des colonnes) est envoyée "
            "au modèle. Les données et les résultats restent ici. Le fichier déposé est temporaire et "
            "n'est jamais conservé. Évite tout de même les noms de colonnes sensibles."
        )
        st.caption("Lecture seule : XK7-SQL ne peut ni modifier ni supprimer de données.")

    st.divider()
    st.subheader("À propos")
    st.caption(
        "XK7 regroupe des agents IA spécialisés. XK7-RAG répond à partir d'une bibliothèque d'ouvrages "
        "sur les bases de données, en citant ses sources. XK7-SQL interroge une base de données en "
        "écrivant lui-même les requêtes SQL."
    )
    st.caption(
        "Sources : un cours universitaire de SQL ; Database Design, A. Watt et N. Eng (CC BY 4.0) ; "
        "Documentation de PostgreSQL 18, The PostgreSQL Global Development Group (licence PostgreSQL) ; "
        "Readings in Database Systems, P. Bailis, J. M. Hellerstein et M. Stonebraker (CC BY-NC-ND) ; "
        "base Chinook, L. Rocha (licence MIT)."
    )

# --- Écran d'accueil
st.title(AGENTS[agent])
if not st.session_state["messages"]:
    if agent == "rag":
        st.markdown(
            "Pose une question sur les bases de données et le langage SQL, du niveau débutant au niveau expert. "
            "L'assistant cherche les passages pertinents dans sa bibliothèque d'ouvrages, puis rédige une réponse "
            "en citant ses sources. Si l'information n'y est pas, il te le dit."
        )
    else:
        st.markdown(
            "Pose une question en français sur les données, par exemple : *Quels sont les 5 clients qui ont "
            "le plus dépensé ?* ou *Quel est le genre musical le plus vendu ?* L'assistant écrit la requête SQL, "
            "l'exécute et affiche le résultat. Tu peux aussi déposer ton propre fichier dans la barre latérale."
        )

# --- Historique de la conversation
for i, msg in enumerate(st.session_state["messages"]):
    with st.chat_message(msg["role"], avatar=AVATARS[msg["role"]]):
        st.markdown(msg["content"])
        if msg["role"] == "assistant":
            show_sources(msg.get("sources", []))
            show_sql_result(msg, i)

# --- Nouvelle question
if agent == "sql" and db_path is None:
    st.info("Dépose un fichier dans la barre latérale pour commencer.")
elif question := st.chat_input("Pose ta question..."):
    if st.session_state["nb_questions"] >= MAX_QUESTIONS:
        st.warning("Limite de questions atteinte pour cette visite.")
        st.stop()
    st.session_state["nb_questions"] += 1

    # L'historique donné à XK7-RAG ne contient que ses propres échanges
    history = [m for m in st.session_state["messages"] if m.get("agent", "rag") == "rag"]
    st.session_state["messages"].append({"role": "user", "content": question, "agent": agent})
    with st.chat_message("user", avatar=AVATARS["user"]):
        st.markdown(question)

    with st.chat_message("assistant", avatar=AVATARS["assistant"]):
        if agent == "rag":
            with st.spinner("Recherche dans les documents..."):
                reply, hits = answer(question, k=k, history=history, level=level)
            # On ne garde que les passages réellement cités dans la réponse
            sources = [] if REFUS in reply else keep_cited(hits, reply)
            st.markdown(reply)
            show_sources(sources)
            new_msg = {"role": "assistant", "agent": "rag", "content": reply, "sources": sources}
        else:
            with st.spinner("Écriture et exécution de la requête..."):
                result = ask(question, db_path)
            if result["ok"]:
                n = len(result["rows"])
                content = f"{result['explanation']}\n\n*Base : {db_label} · {n} ligne{'s' if n > 1 else ''}*"
                if result.get("warning"):
                    content += f"\n\n**{result['warning']}**"
                new_msg = {"role": "assistant", "agent": "sql", "content": content, "sql": result["sql"],
                           "columns": result["columns"], "rows": result["rows"],
                           "truncated": result["truncated"]}
            else:
                new_msg = {"role": "assistant", "agent": "sql", "content": result["message"]}
            st.markdown(new_msg["content"])
            show_sql_result(new_msg, len(st.session_state["messages"]))

    st.session_state["messages"].append(new_msg)
