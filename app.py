import os
import pandas as pd
import streamlit as st
from dotenv import load_dotenv
from rag import answer, keep_cited, LEVELS, REFUS
from sql_agent import ask, MAX_ROWS
from core import run
from quality_agent import check_quality, summary as quality_summary
from sql_schema import DB_PATH
from data_upload import to_sqlite, delete_file, UploadError, MAX_SIZE_MB

load_dotenv()

MAX_QUESTIONS = 20  # nombre maximum de questions par visite
AVATARS = {
    "user": ":material/person:",
    "assistant": ":material/database:",
}
AGENTS = {
    "core": "Automatique",
    "rag": "Documentation",
    "sql": "Données",
    "qualite": "Qualité",
}
# Exemples cliquables sur l'écran d'accueil
EXAMPLES = {
    "core": ["C'est quoi une jointure externe ?", "Quel genre musical se vend le mieux ?",
             "Explique le GROUP BY, puis donne le chiffre d'affaires par pays."],
    "rag": ["C'est quoi une jointure externe ?", "Quelles sont les règles de la deuxième forme normale ?",
            "À quoi sert un index partiel ?"],
    "sql": ["Quel genre musical se vend le mieux ?", "Quels sont les 5 clients qui ont le plus dépensé ?",
            "Quels employés n'ont aucun client ?"],
    "qualite": ["Vérifie la qualité des données.", "Y a-t-il des doublons dans les clients ?",
                "Les données des factures sont-elles fiables ?"],
}
WELCOME = {
    "core": "Pose une question de cours ou sur les données : le bon agent répond.",
    "rag": "Pose une question de cours sur les bases de données et le SQL.",
    "sql": "Pose une question sur les données : la requête SQL est écrite et exécutée pour toi.",
    "qualite": "Demande un contrôle : doublons, valeurs manquantes, formats, liens cassés, règles métier.",
}
ROUTE_LABELS = {"rag": "XK7-RAG", "sql": "XK7-SQL", "qualite": "XK7-QUALITE", "les_deux": "XK7-RAG puis XK7-SQL",
                "hors_sujet": "aucun agent"}
GRAVITY_ICONS = {"erreur": ":material/error:", "à vérifier": ":material/warning:", "info": ":material/info:"}

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


def show_message(msg, key):
    """Affiche une réponse : choix de XK7-CORE, partie cours, partie données."""
    if msg.get("route"):
        st.caption(f":material/alt_route: XK7-CORE → {msg['route']}")
    st.markdown(msg["content"])
    show_sources(msg.get("sources", []))
    show_sql_result(msg, key)
    show_quality(msg, key)


def show_quality(msg, key):
    """Affiche le rapport de XK7-QUALITE : un encadré par table, puis les règles métier."""
    report = msg.get("quality")
    if not report:
        return
    for table, r in sorted(report["tables"].items(), key=lambda x: x[1]["score"]):
        problems = [p for p in r["problemes"]]
        serious = [p for p in problems if p["gravite"] != "info"]
        label = f"{table} · {r['score']}/100 · {len(serious)} problème{'s' if len(serious) > 1 else ''}"
        with st.expander(label, icon=":material/table:", expanded=False):
            if not problems:
                st.caption("Aucun problème détecté.")
            for p in problems:
                col = f" · {p['colonne']}" if p.get("colonne") else ""
                st.markdown(f"{GRAVITY_ICONS.get(p['gravite'], '')} **{p['type']}**{col} : {p['detail']}")
                if p.get("exemples"):
                    st.dataframe(pd.DataFrame(p["exemples"]), hide_index=True, width="stretch")
                fix = p.get("correction")
                if fix and p["gravite"] != "info":
                    st.caption(f":material/build: {fix['conseil']}")
                    if fix.get("sql") and not fix["sql"].lstrip().upper().startswith("SELECT"):
                        st.code(fix["sql"], language="sql")
    if report.get("script"):
        with st.expander("Script de correction proposé", icon=":material/build:"):
            st.caption("Écrit par le code à partir du rapport. XK7 ne l'exécute jamais : relis-le, puis lance-le "
                       "toi-même sur une copie de la base. Les corrections sûres sont actives, les autres sont en "
                       "commentaire avec un conseil.")
            st.code(report["script"], language="sql")
            st.download_button("Télécharger le script (.sql)", report["script"].encode("utf-8"),
                               file_name="corrections_xk7.sql", mime="text/plain", key=f"fix_{key}",
                               icon=":material/download:")
    if report.get("regles"):
        with st.expander("Règles métier proposées", icon=":material/rule:"):
            for r in report["regles"]:
                count = f" ({r['nb']} ligne{'s' if r.get('nb', 0) > 1 else ''})" if r.get("nb") else ""
                st.markdown(f"- {r['regle']} : **{r['statut']}**{count}")
                if r.get("sql"):
                    st.code(r["sql"], language="sql")


def quality_message(report, db_label):
    """Transforme un rapport de XK7-QUALITE en message à afficher (résumé écrit par le code, sans IA)."""
    text = quality_summary(report).replace("\n- ", "  \n- ")
    return {"content": f"{text}\n\n*Base : {db_label} · contrôles faits en local, seules les règles métier "
                       "sont proposées par le modèle à partir de la structure*", "quality": report}


def sql_message(result, db_label):
    """Transforme un résultat de XK7-SQL en message à afficher."""
    if not result["ok"]:
        return {"content": result["message"]}
    n = len(result["rows"])
    content = f"{result['explanation']}\n\n*Base : {db_label} · {n} ligne{'s' if n > 1 else ''}*"
    if result.get("warning"):
        content += f"\n\n**{result['warning']}**"
    return {"content": content, "sql": result["sql"], "columns": result["columns"],
            "rows": result["rows"], "truncated": result["truncated"]}


def reset_upload():
    """Supprime le fichier temporaire de l'utilisateur."""
    if st.session_state["upload"]:
        delete_file(st.session_state["upload"]["path"])
        st.session_state["upload"] = None


# --- Barre latérale
with st.sidebar:
    st.caption("XK7 · Assistant IA pour les données")

    if st.button("Nouvelle conversation", icon=":material/add:", width="stretch"):
        st.session_state["messages"] = []
        st.rerun()

    st.divider()
    agent = st.radio(
        "Agent", list(AGENTS), format_func=AGENTS.get,
        help="Automatique : XK7-CORE choisit l'agent. Documentation : XK7-RAG répond avec les ouvrages. "
             "Données : XK7-SQL interroge la base. Qualité : XK7-QUALITE contrôle les données.",
    )

    st.divider()
    st.subheader("Réglages")
    db_path, db_label = DB_PATH, "Chinook (démonstration)"
    level, k = "Intermédiaire", 3
    if agent in ("core", "rag"):
        level = st.radio(
            "Niveau des réponses",
            list(LEVELS),
            index=1,
            help="Adapte la façon d'expliquer : même réponse, plus ou moins vulgarisée. "
                 "Les informations viennent toujours des documents.",
        )
        k = st.slider(
            "Nombre de passages consultés",
            1, 10, 3,
            help="Nombre d'extraits lus avant de répondre. 1 à 3 : réponse ciblée. 5 à 10 : utile si la "
                 "réponse est répartie dans les documents, ou si l'assistant ne trouve pas.",
        )
    if agent in ("core", "sql", "qualite"):
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
            ":material/lock: Confidentiel et en lecture seule",
            help="Seule la structure de la base (noms des tables et des colonnes) est envoyée au modèle : "
                 "les données et les résultats restent ici. Le fichier déposé est temporaire et n'est jamais "
                 "conservé. XK7-SQL ne peut ni modifier ni supprimer de données.",
        )

    st.divider()
    with st.expander("À propos"):
        st.caption(
            "XK7 regroupe des agents IA spécialisés. XK7-CORE lit la question et choisit le bon agent, ou les "
            "enchaîne. XK7-RAG répond à partir d'une bibliothèque d'ouvrages sur les bases de données, en citant "
            "ses sources. XK7-SQL interroge une base de données en écrivant lui-même les requêtes SQL. "
            "XK7-QUALITE contrôle la qualité des données et propose des règles métier."
        )
        st.caption(
            "Sources : un cours universitaire de SQL ; Database Design, A. Watt et N. Eng (CC BY 4.0) ; "
            "Documentation de PostgreSQL 18, The PostgreSQL Global Development Group (licence PostgreSQL) ; "
            "Readings in Database Systems, P. Bailis, J. M. Hellerstein et M. Stonebraker (CC BY-NC-ND) ; "
            "base Chinook, L. Rocha (licence MIT)."
        )

# --- Écran d'accueil
st.title("XK7")
pending = None
if not st.session_state["messages"]:
    st.markdown(WELCOME[agent])
    st.caption("Exemples :")
    for i, example in enumerate(EXAMPLES[agent]):
        if st.button(example, key=f"ex_{agent}_{i}", icon=":material/arrow_forward:"):
            pending = example

# --- Historique de la conversation
for i, msg in enumerate(st.session_state["messages"]):
    with st.chat_message(msg["role"], avatar=AVATARS[msg["role"]]):
        if msg["role"] == "assistant":
            show_message(msg, i)
            for j, part in enumerate(msg.get("parts", [])):
                show_message(part, f"{i}_{j}")
        else:
            st.markdown(msg["content"])

# --- Nouvelle question
if agent in ("core", "sql", "qualite") and db_path is None:
    st.info("Dépose un fichier dans la barre latérale pour commencer.")
elif (question := st.chat_input("Pose ta question...") or pending):
    if st.session_state["nb_questions"] >= MAX_QUESTIONS:
        st.warning("Limite de questions atteinte pour cette visite.")
        st.stop()
    st.session_state["nb_questions"] += 1

    previous = list(st.session_state["messages"])
    # L'historique donné à XK7-RAG ne contient que ses propres échanges
    history = [m for m in previous if m.get("agent", "rag") == "rag"]
    st.session_state["messages"].append({"role": "user", "content": question, "agent": agent})
    with st.chat_message("user", avatar=AVATARS["user"]):
        st.markdown(question)

    with st.chat_message("assistant", avatar=AVATARS["assistant"]):
        if agent == "core":
            with st.spinner("XK7-CORE choisit l'agent..."):
                source = (f"le fichier déposé par l'utilisateur ({db_label})" if db_path != DB_PATH
                          else "la base de démonstration Chinook")
                result = run(question, previous, db_path, level, k, source)
            chosen = result["agent"]
            route_text = ROUTE_LABELS[chosen] + (f" · {result['reason']}" if result.get("reason") else "")
            standalone = result.get("standalone", question)
            if standalone.strip() != question.strip() and chosen != "les_deux":
                route_text += f"  \nQuestion comprise : *{standalone}*"
            if chosen == "les_deux":
                route_text += (f"  \nPartie cours : *{result['rag_question']}*  \n"
                               f"Partie données : *{result['sql_question']}*")
                if not result.get("split_ok"):
                    route_text += "  \nDécoupage incomplet : la question entière a été utilisée pour une partie."
            parts = []
            if result.get("rag"):
                reply = result["rag"]["reply"]
                sources = [] if REFUS in reply else keep_cited(result["rag"]["hits"], reply)
                parts.append({"content": reply, "sources": sources})
            if result.get("sql"):
                parts.append(sql_message(result["sql"], db_label))
            if result.get("qualite"):
                parts.append(quality_message(result["qualite"], db_label))
            if result.get("message"):
                parts.append({"content": result["message"]})
            new_msg = {"role": "assistant", "agent": chosen, **parts[0], "route": route_text,
                       "parts": parts[1:]}
            # Pour la mémoire de XK7-RAG : la question de l'utilisateur prend l'agent choisi
            st.session_state["messages"][-1]["agent"] = chosen
            key = len(st.session_state["messages"])
            show_message(new_msg, key)
            for j, part in enumerate(new_msg["parts"]):
                show_message(part, f"{key}_{j}")
        elif agent == "qualite":
            with st.spinner("Contrôle de la qualité des données..."):
                report = check_quality(question, db_path)
            new_msg = {"role": "assistant", "agent": "qualite", **quality_message(report, db_label)}
            show_message(new_msg, len(st.session_state["messages"]))
        elif agent == "rag":
            with st.spinner("Recherche dans les documents..."):
                reply, hits = answer(question, k=k, history=history, level=level)
            # On ne garde que les passages réellement cités dans la réponse
            sources = [] if REFUS in reply else keep_cited(hits, reply)
            new_msg = {"role": "assistant", "agent": "rag", "content": reply, "sources": sources}
            show_message(new_msg, len(st.session_state["messages"]))
        else:
            with st.spinner("Écriture et exécution de la requête..."):
                result = ask(question, db_path)
            new_msg = {"role": "assistant", "agent": "sql", **sql_message(result, db_label)}
            show_message(new_msg, len(st.session_state["messages"]))

    st.session_state["messages"].append(new_msg)
    if pending:
        st.rerun()   # efface l'écran d'accueil après un exemple cliqué
