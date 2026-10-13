import streamlit as st
from rag import answer

st.set_page_config(page_title="XK7-RAG", page_icon="🤖")
st.title("🤖 XK7-RAG")
st.caption("Pose une question sur le cours de bases de données et SQL.")

# Curseur dans la barre latérale : nombre de passages consultés (de 1 à 10, 3 par défaut)
k = st.sidebar.slider("Nombre de passages consultés", 1, 10, 3)

question = st.text_input("Ta question")

if question:
    with st.spinner("Recherche dans le cours..."):
        reply, hits = answer(question, k=k)

    st.markdown(reply)

    with st.expander("Passages consultés"):
        for h in hits:
            st.markdown(f"**Page {h['page']}** (distance {h['distance']:.3f})")
            st.write(h["text"])