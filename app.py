import os
import streamlit as st
from dotenv import load_dotenv
from rag import answer

load_dotenv()

MAX_QUESTIONS = 20  # nombre maximum de questions par visite

st.set_page_config(page_title="XK7-RAG")
st.title("XK7-RAG")
st.caption("Pose une question sur le cours de bases de données et SQL.")

# Mot de passe : demandé seulement s'il est défini (dans .env ou dans les secrets en ligne)
password = os.getenv("APP_PASSWORD")
if password and not st.session_state.get("auth"):
    entered = st.text_input("Mot de passe", type="password")
    if entered == password:
        st.session_state["auth"] = True
        st.rerun()
    elif entered:
        st.error("Mot de passe incorrect.")
    st.stop()

# Curseur dans la barre latérale : nombre de passages consultés
st.sidebar.header("Réglages")
k = st.sidebar.slider(
    "Nombre de passages consultés",
    1, 10, 3,
    help="Nombre d'extraits du cours que l'assistant lit avant de répondre.",
)
st.sidebar.caption(
    "Avant de répondre, l'assistant cherche dans le cours les passages les plus proches "
    "de ta question, puis rédige sa réponse uniquement à partir de ces passages.\n\n"
    "Peu de passages (1 à 3) : réponse plus ciblée.\n\n"
    "Plus de passages (5 à 10) : utile si la réponse est répartie sur plusieurs parties du cours, "
    "mais la réponse peut être moins précise.\n\n"
    "Si l'assistant ne trouve pas la réponse, essaie d'augmenter ce nombre ou de reformuler ta question."
)

# Compteur de questions pour cette visite
st.session_state.setdefault("nb_questions", 0)

with st.form("question_form"):
    question = st.text_input("Ta question")
    submitted = st.form_submit_button("Envoyer")

if submitted and question:
    if st.session_state["nb_questions"] >= MAX_QUESTIONS:
        st.warning("Limite de questions atteinte pour cette visite.")
        st.stop()
    st.session_state["nb_questions"] += 1

    with st.spinner("Recherche dans le cours..."):
        reply, hits = answer(question, k=k)

    st.markdown(reply)

    with st.expander("Passages consultés"):
        for h in hits:
            st.markdown(f"**Page {h['page']}** (distance {h['distance']:.3f})")
            st.write(h["text"])