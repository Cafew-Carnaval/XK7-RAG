import os
import streamlit as st
from dotenv import load_dotenv
from rag import answer, cited_pages, LEVELS

load_dotenv()

MAX_QUESTIONS = 20  # nombre maximum de questions par visite
REFUS = "Je ne trouve pas la réponse dans le cours"
AVATARS = {
    "user": ":material/person:",
    "assistant": ":material/database:",
}

st.set_page_config(page_title="XK7-RAG", page_icon=":material/database:")

# --- Mot de passe : demandé seulement s'il est défini (dans .env ou dans les secrets en ligne)
password = os.getenv("APP_PASSWORD")
if password and not st.session_state.get("auth"):
    st.title("XK7-RAG")
    entered = st.text_input("Mot de passe", type="password")
    if entered == password:
        st.session_state["auth"] = True
        st.rerun()
    elif entered:
        st.error("Mot de passe incorrect.")
    st.stop()

# --- Mémoire de la conversation
st.session_state.setdefault("messages", [])
st.session_state.setdefault("nb_questions", 0)


def show_sources(sources):
    """Affiche les passages cités, avec leur section et leur page."""
    if not sources:
        return
    with st.expander(f"Sources ({len(sources)})"):
        for h in sources:
            text = h["text"]
            if text.startswith("Section :"):  # on retire la ligne de titre ajoutée au découpage
                text = text.split("\n", 1)[1] if "\n" in text else ""
            section = " > ".join(h.get("section", "").split(" > ")[-2:])
            st.markdown(f"**{section}** · page {h['page']}")
            st.caption(text[:600] + ("…" if len(text) > 600 else ""))


# --- Barre latérale
with st.sidebar:
    st.title("XK7-RAG")
    st.caption("Assistant IA pour la documentation data")

    if st.button("Nouvelle conversation", icon=":material/add:", width="stretch"):
        st.session_state["messages"] = []
        st.rerun()

    st.divider()
    st.subheader("Réglages")
    level = st.radio(
        "Niveau des réponses",
        list(LEVELS),
        index=1,
        captions=["Grand public", "Bac+1 à bac+5", "Bac+6 et plus"],
        help="Adapte la façon d'expliquer : même réponse, plus ou moins vulgarisée. Les informations viennent toujours du cours.",
    )
    k = st.slider(
        "Nombre de passages consultés",
        1, 10, 3,
        help="Nombre d'extraits du cours que l'assistant lit avant de répondre.",
    )
    st.caption(
        "Peu de passages (1 à 3) : réponse plus ciblée. "
        "Plus de passages (5 à 10) : utile si la réponse est répartie sur plusieurs parties du cours. "
        "Si l'assistant ne trouve pas, augmente ce nombre ou reformule ta question."
    )

    st.divider()
    st.subheader("À propos")
    st.caption(
        "XK7-RAG répond à partir d'un cours sur les bases de données et SQL. "
        "Chaque réponse cite ses pages, et l'assistant dit quand il ne sait pas plutôt que d'inventer."
    )
    

# --- Écran d'accueil
st.title("XK7-RAG")
if not st.session_state["messages"]:
    st.markdown(
        "Pose une question sur les bases de données et le langage SQL. "
        "L'assistant cherche les passages pertinents dans le cours, puis rédige une réponse "
        "en citant ses sources. Si l'information n'est pas dans le cours, il te le dit."
    )

# --- Historique de la conversation
for msg in st.session_state["messages"]:
    with st.chat_message(msg["role"], avatar=AVATARS[msg["role"]]):
        st.markdown(msg["content"])
        if msg["role"] == "assistant":
            show_sources(msg.get("sources", []))

# --- Nouvelle question
if question := st.chat_input("Pose ta question..."):
    if st.session_state["nb_questions"] >= MAX_QUESTIONS:
        st.warning("Limite de questions atteinte pour cette visite.")
        st.stop()
    st.session_state["nb_questions"] += 1

    history = list(st.session_state["messages"])
    st.session_state["messages"].append({"role": "user", "content": question})
    with st.chat_message("user", avatar=AVATARS["user"]):
        st.markdown(question)

    with st.chat_message("assistant", avatar=AVATARS["assistant"]):
        with st.spinner("Recherche dans le cours..."):
            reply, hits = answer(question, k=k, history=history, level=level)

        # On ne garde que les passages réellement cités dans la réponse
        if REFUS in reply:
            sources = []
        else:
            pages = cited_pages(reply)
            sources = [h for h in hits if h["page"] in pages]

        st.markdown(reply)
        show_sources(sources)

    st.session_state["messages"].append(
        {"role": "assistant", "content": reply, "sources": sources}
    )
