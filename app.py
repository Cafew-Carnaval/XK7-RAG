import os
import contextvars
import numpy as np
import pandas as pd
import altair as alt
import streamlit as st
from dotenv import load_dotenv
from rag import answer, keep_cited, LEVELS, REFUS
from sql_agent import ask, MAX_ROWS
from core import run
from quality_agent import check_quality, summary as quality_summary
from analyse_agent import analyse as run_analysis
from prevision_agent import (prevoir, run_plan as run_forecast_plan, fmt as pfmt, pct as ppct, label as plabel,
                             readable)
from sql_schema import DB_PATH
from data_upload import to_sqlite, delete_file, UploadError, MAX_SIZE_MB
import router, core, sql_agent, quality_agent, analyse_agent, prevision_agent

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
    "analyse": "Analyse",
    "prevision": "Prévision",
}
# Exemples cliquables sur l'écran d'accueil
EXAMPLES = {
    "core": ["C'est quoi une jointure externe ?", "Comment évolue le chiffre d'affaires par année ?",
             "Explique le GROUP BY, puis donne le chiffre d'affaires par pays."],
    "rag": ["C'est quoi une jointure externe ?", "Quelles sont les règles de la deuxième forme normale ?",
            "À quoi sert un index partiel ?"],
    "sql": ["Quel genre musical se vend le mieux ?", "Quels sont les 5 clients qui ont le plus dépensé ?",
            "Quels employés n'ont aucun client ?"],
    "qualite": ["Vérifie la qualité des données.", "Y a-t-il des doublons dans les clients ?",
                "Les données des factures sont-elles fiables ?"],
    "analyse": ["Comment évolue le chiffre d'affaires par année ?", "Quelle est la répartition des morceaux par genre ?",
                "Y a-t-il un lien entre la durée d'un morceau et sa taille ?"],
    "prevision": ["Prévois le chiffre d'affaires des 6 prochains mois.", "Combien de factures le trimestre prochain ?",
                  "Comment va évoluer le panier moyen ?"],
}
WELCOME = {
    "core": "Pose une question de cours ou sur les données : le bon agent répond.",
    "rag": "Pose une question de cours sur les bases de données et le SQL.",
    "sql": "Pose une question sur les données : la requête SQL est écrite et exécutée pour toi.",
    "qualite": "Demande un contrôle : doublons, valeurs manquantes, formats, liens cassés, règles métier.",
    "analyse": "Demande une analyse avec graphique : évolution, répartition, comparaison, lien entre deux valeurs.",
    "prevision": "Demande une prévision : 6 modèles sont mis en concurrence sur le passé, le meilleur prédit la suite.",
}
ROUTE_LABELS = {"rag": "XK7-RAG", "sql": "XK7-SQL", "qualite": "XK7-QUALITE", "analyse": "XK7-ANALYSE", "prevision": "XK7-PREVISION", "les_deux": "XK7-RAG puis XK7-SQL",
                "hors_sujet": "aucun agent"}
# Couleurs du thème XK7 (voir .streamlit/config.toml), reprises dans les graphiques
XK7 = {"orange": "#FF8A3D", "rose": "#F5A3C7", "texte": "#F4EEF2", "grille": "#3A2533", "bleu": "#5CC8FF"}
# Cartes de l'écran d'accueil en mode automatique : icône, nom, rôle, exemple
CARDS = [
    (":material/menu_book:", "Documentation", "Cours et documentation SQL", "C'est quoi une jointure externe ?"),
    (":material/table_view:", "Données", "Questions en français sur la base", "Quels sont les 5 clients qui ont le plus dépensé ?"),
    (":material/fact_check:", "Qualité", "Doublons, trous, incohérences", "Vérifie la qualité des données."),
    (":material/monitoring:", "Analyse", "Évolutions, répartitions, liens", "Comment évolue le chiffre d'affaires par année ?"),
    (":material/trending_up:", "Prévision", "Le futur, d'après le passé", "Prévois le chiffre d'affaires des 6 prochains mois."),
]
GRAVITY_ICONS = {"erreur": ":material/error:", "à vérifier": ":material/warning:", "info": ":material/info:"}

st.set_page_config(page_title="XK7", page_icon=":material/database:")

# --- « Ce que l'IA a vu » : capture, pour CETTE session seulement, de tout ce qui est envoyé aux modèles.
# Les journaux des agents sont partagés par tous les visiteurs de l'app en ligne : on les remplace par des
# journaux qui ne gardent rien en commun et ne remplissent que la capture de la session en cours.
CAPTURE = contextvars.ContextVar("xk7_capture", default=None)


class SessionLog(list):
    def __init__(self, agent):
        super().__init__()
        self.agent = agent

    def append(self, prompt):
        captured = CAPTURE.get()
        if captured is not None:
            captured.append({"agent": self.agent, "prompt": prompt})


for module, name, label in [(router, "ROUTER_LOG", "XK7-CORE · routage"), (core, "SPLIT_LOG", "XK7-CORE · découpage"),
                            (sql_agent, "PROMPT_LOG", "XK7-SQL"), (quality_agent, "RULES_LOG", "XK7-QUALITE"),
                            (analyse_agent, "PLAN_LOG", "XK7-ANALYSE"), (prevision_agent, "PLAN_LOG", "XK7-PREVISION")]:
    if not isinstance(getattr(module, name), SessionLog):
        setattr(module, name, SessionLog(label))

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
    if msg.get("write_sql"):
        st.markdown(":material/edit_note: **Requête proposée** (non exécutée)")
        st.code(msg["write_sql"], language="sql")
        st.caption(f"Lignes qu'elle toucherait : {len(msg['rows'])}{' ou plus' if msg.get('truncated') else ''}")
    if not msg.get("columns"):
        return
    df = pd.DataFrame(msg["rows"], columns=msg["columns"])
    if df.shape == (1, 1):                       # une seule valeur ("combien de...") : affichée en gros
        value = df.iloc[0, 0]
        st.metric(df.columns[0], f"{value:,}".replace(",", " ") if isinstance(value, (int, float)) else value)
    else:
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
    if msg.get("badge"):
        st.markdown(f":orange-badge[{msg['badge']}]")
    st.markdown(msg["content"])
    show_sources(msg.get("sources", []))
    show_sql_result(msg, key)
    show_quality(msg, key)
    show_analysis(msg, key)
    show_forecast(msg, key)
    show_sent(msg)


def show_sent(msg):
    """Ce que l'IA a vu : les prompts exacts envoyés aux modèles pour cette réponse."""
    if "sent" not in msg:
        return
    with st.expander("Ce que l'IA a vu", icon=":material/visibility:"):
        st.caption("Le texte exact envoyé aux modèles pour cette réponse : ta question, la structure de la base "
                   "(noms des tables et des colonnes, nombre de lignes) et les consignes. Aucune valeur de la base, aucun résultat.")
        if msg.get("agent_rag"):
            st.caption("XK7-RAG envoie aussi ta question et des passages des ouvrages de la bibliothèque "
                       "(jamais de données de la base).")
        if not msg["sent"]:
            st.caption("Aucun appel à un modèle pour cette réponse.")
        for i, item in enumerate(msg["sent"], 1):
            st.markdown(f"**{i}. {item['agent']}** · {len(item['prompt'])} caractères")
            st.code(item["prompt"], language="text", wrap_lines=True)


def show_chart(chart):
    """Graphique de XK7-ANALYSE, dans l'ordre calculé par le code (pas d'ordre alphabétique imposé)."""
    if chart["kind"] == "scatter":
        df = chart["data"].set_axis(["x", "y"], axis=1)
        c = alt.Chart(df).mark_circle(opacity=0.45, color=XK7["orange"]).encode(
            x=alt.X("x:Q", title=chart["x"]), y=alt.Y("y:Q", title=chart["y"]))
    else:
        series = chart["data"]
        if chart["kind"] == "line":
            # Axe des dates : le graphique n'affiche que quelques étiquettes lisibles, même sur 60 mois
            df = pd.DataFrame({"label": series.index, "value": series.values})
            date_format = chart.get("format", "%m/%Y")
            points = {"filled": True, "color": XK7["rose"], "size": 45} if len(df) <= 36 else False
            c = alt.Chart(df).mark_line(color=XK7["orange"], strokeWidth=3, point=points).encode(
                x=alt.X("label:T", title=series.index.name, axis=alt.Axis(format=date_format, labelAngle=0, labelOverlap="greedy", tickCount=8)),
                y=alt.Y("value:Q", title=series.name),
                tooltip=[alt.Tooltip("label:T", title=series.index.name, format=date_format),
                         alt.Tooltip("value:Q", title=series.name, format=",.2f")])
        else:
            df = pd.DataFrame({"label": series.index.astype(str), "value": series.values})
            c = alt.Chart(df).mark_bar(color=XK7["orange"], cornerRadiusEnd=4).encode(
                y=alt.Y("label:N", sort=None, title=series.index.name), x=alt.X("value:Q", title=series.name),
                tooltip=[alt.Tooltip("label:N", title=series.index.name), alt.Tooltip("value:Q", title=series.name)]
            ).properties(height=max(160, 26 * len(df)))
    c = (c.properties(title=chart.get("title", ""))
         .configure(background="transparent", font="sans-serif")
         .configure_axis(labelColor=XK7["rose"], titleColor=XK7["rose"], gridColor=XK7["grille"],
                         domainColor=XK7["grille"], tickColor=XK7["grille"], labelFontSize=12, titleFontSize=12)
         .configure_title(color=XK7["texte"], fontSize=15, anchor="start")
         .configure_view(strokeWidth=0))
    st.altair_chart(c, width="stretch", theme=None)


def show_analysis(msg, key):
    """Affiche le résultat de XK7-ANALYSE : graphique, tableau, téléchargement et requête d'extraction."""
    result = msg.get("analysis")
    if not result:
        return
    metrics = result["chart"].get("metrics", [])
    for col, m in zip(st.columns(len(metrics)) if metrics else [], metrics):
        col.metric(m["label"], m["value"], m.get("delta"), delta_color=m.get("delta_color", "normal"))
    show_chart(result["chart"])
    with st.expander("Tableau des résultats", icon=":material/table:"):
        st.dataframe(result["table"], hide_index=True, width="stretch")
        st.download_button("Télécharger en CSV", result["table"].to_csv(index=False).encode("utf-8"),
                           file_name="analyse.csv", mime="text/csv", key=f"an_{key}", icon=":material/download:")
    with st.expander("Requête d'extraction"):
        st.caption(result.get("explication", ""))
        st.code(result["sql"], language="sql")


def xk7_style(chart, title=""):
    """Couleurs et polices du thème XK7 pour un graphique Altair."""
    return (chart.properties(title=title)
            .configure(background="transparent", font="sans-serif")
            .configure_axis(labelColor=XK7["rose"], titleColor=XK7["rose"], gridColor=XK7["grille"],
                            domainColor=XK7["grille"], tickColor=XK7["grille"], labelFontSize=12, titleFontSize=12)
            .configure_title(color=XK7["texte"], fontSize=15, anchor="start")
            .configure_legend(labelColor=XK7["texte"], titleColor=XK7["rose"])
            .configure_view(strokeWidth=0))


def forecast_message(result, db_label):
    """Transforme un résultat de XK7-PREVISION en message à afficher (synthèse écrite par le code, sans IA)."""
    if result["type"] in ("impossible", "bientot", "echec", "refus"):
        texts = {"refus": "Je ne peux pas faire ça : XK7-PREVISION est en lecture seule.",
                 "echec": "Je n'ai pas réussi à préparer cette prévision. Précise la valeur à prévoir (prix, ventes, "
                          "commandes...) et, si besoin, la période (par mois, par trimestre)."}
        return {"badge": "XK7-PREVISION", "content": texts.get(result["type"], result["erreur"])}
    hist = None
    if result.get("distribution") is not None and len(result["distribution"]):
        d = result["distribution"]
        d = d[d <= d.quantile(0.99)]              # le 1 % le plus haut écraserait l'échelle : on ne le dessine pas
        counts, edges = np.histogram(d, bins=30)
        hist = pd.DataFrame({"de": edges[:-1], "a": edges[1:], "n": counts})
    keep = ("serie", "niveau", "periode", "cible", "contexte", "synthese", "modeles", "gagnant", "utilise",
            "bat_le_naif", "critere", "backtest", "prevision", "plan", "donnees")
    content = (f"**{result['plan'].get('titre') or result['cible']}**\n\n*Base : {db_label} · "
               f"{len(result['donnees'])} lignes · modèles entraînés en local, synthèse écrite par le code*")
    return {"badge": "XK7-PREVISION", "content": content,
            "forecast": {**{k: result[k] for k in keep if k in result}, "histogramme": hist}}


def show_forecast(msg, key):
    """XK7-PREVISION en 4 blocs : contexte, prévision, justification (les modèles), synthèse ; réglages modifiables."""
    f = msg.get("forecast")
    if not f:
        return
    series, periode = f["serie"], f["periode"]
    history = pd.DataFrame({"date": series.index.to_timestamp(), "valeur": series.values})
    date_format = "%m/%Y" if periode == "mois" else "%Y"
    # Par trimestre : une seule étiquette par an (le 1er janvier), sinon chaque année s'affiche quatre fois
    years = range(history["date"].dt.year.min(), history["date"].dt.year.max() + 2)
    axis = (alt.Axis(format=date_format, labelOverlap="greedy") if periode == "mois" else
            alt.Axis(format="%Y", values=[alt.DateTime(year=y, month=1, date=1) for y in years]))

    # 1. Le contexte : ce qui s'est passé avant
    st.markdown("**1. Ce qui s'est passé**")
    season = 12 if periode == "mois" else 4
    cols = st.columns(3)
    cols[0].metric(f"Dernière période ({plabel(series.index[-1], periode)})", pfmt(series.iloc[-1]))
    cols[1].metric(f"Plus haut ({plabel(series.idxmax(), periode)})", pfmt(series.max()))
    if len(series) > season:
        change = series.iloc[-1] / series.iloc[-season - 1] - 1
        cols[2].metric("Sur un an", f"{'+' if change >= 0 else ''}{ppct(change)}")
    if f.get("histogramme") is not None:
        h = f["histogramme"]
        bars = alt.Chart(h).mark_bar(color=XK7["rose"], opacity=0.85).encode(
            x=alt.X("de:Q", bin="binned", title=readable(f.get("plan", {}).get("cible")) or "Valeur"),
            x2="a:Q", y=alt.Y("n:Q", title="Nombre de lignes"),
            tooltip=[alt.Tooltip("de:Q", title="de", format=",.0f"), alt.Tooltip("a:Q", title="à", format=",.0f"),
                     alt.Tooltip("n:Q", title="lignes")]).properties(height=180)
        st.altair_chart(xk7_style(bars, "Répartition des valeurs de départ"), width="stretch", theme=None)

    # 2. La prévision : l'historique en rose, la suite en bleu pointillé, la zone d'incertitude
    st.markdown("**2. La prévision**")
    if "prevision" not in f:
        line = alt.Chart(history).mark_line(color=XK7["rose"], strokeWidth=3).encode(
            x=alt.X("date:T", title=None, axis=axis),
            y=alt.Y("valeur:Q", title=f["cible"], scale=alt.Scale(zero=False)))
        st.altair_chart(xk7_style(line, "Évolution observée"), width="stretch", theme=None)
    else:
        p = f["prevision"]
        start = pd.DataFrame({"date": [history["date"].iloc[-1]], "valeur": [history["valeur"].iloc[-1]],
                              "bas": [history["valeur"].iloc[-1]], "haut": [history["valeur"].iloc[-1]]})
        future = pd.concat([start, pd.DataFrame({"date": p["Période"].dt.to_timestamp(), "valeur": p["Prévision"],
                                                 "bas": p["Bas"], "haut": p["Haut"]})], ignore_index=True)
        x = alt.X("date:T", title=None, axis=axis)
        y = alt.Y("valeur:Q", title=f["cible"], scale=alt.Scale(zero=False))
        past = alt.Chart(history).mark_line(color=XK7["rose"], strokeWidth=3).encode(
            x=x, y=y, tooltip=[alt.Tooltip("date:T", format=date_format, title="Période"),
                               alt.Tooltip("valeur:Q", format=",.0f", title="Observé")])
        band = alt.Chart(future).mark_area(color=XK7["bleu"], opacity=0.18).encode(x=x, y="bas:Q", y2="haut:Q")
        ahead = alt.Chart(future).mark_line(color=XK7["bleu"], strokeWidth=3, strokeDash=[6, 4],
                                            point={"filled": True, "color": XK7["bleu"], "size": 40}).encode(
            x=x, y=y, tooltip=[alt.Tooltip("date:T", format=date_format, title="Période"),
                               alt.Tooltip("valeur:Q", format=",.0f", title="Prévision"),
                               alt.Tooltip("bas:Q", format=",.0f", title="Bas"),
                               alt.Tooltip("haut:Q", format=",.0f", title="Haut")])
        st.altair_chart(xk7_style(band + past + ahead, "Historique (rose) et prévision (bleu)"), width="stretch",
                        theme=None)
        table = pd.DataFrame({"Période": [plabel(q, periode) for q in p["Période"]],
                              "Prévision": [pfmt(v) for v in p["Prévision"]],
                              "Fourchette (80 %)": [f"{pfmt(a)} à {pfmt(b)}" for a, b in zip(p["Bas"], p["Haut"])]})
        st.dataframe(table, hide_index=True, width="stretch")

        # 3. La justification : les 6 modèles et leurs scores sur le passé
        st.markdown("**3. Pourquoi ce modèle**")
        m = f["modeles"].copy()
        m.insert(0, "", ["🏆" if name == f["utilise"] else "" for name in m["Modèle"]])
        m["MAPE"] = m["MAPE"].map(ppct)
        m["MAE"] = m["MAE"].map(pfmt)
        m["RMSE"] = m["RMSE"].map(pfmt)
        m["Gain sur le naïf"] = m["Gain sur le naïf"].map(lambda v: ppct(v, 0))
        st.dataframe(m, hide_index=True, width="stretch")
        st.caption("MAPE : erreur moyenne en %. MAE : erreur moyenne dans l'unité de la valeur. RMSE : pareil, en "
                   "pénalisant les grosses erreurs. Gain sur le naïf : erreur évitée par rapport à « demain = aujourd'hui ».")
        with st.expander("Détail du backtest (prédictions de chaque modèle sur le passé)"):
            st.dataframe(f["backtest"].round(0), hide_index=True, width="stretch")

    # 4. La synthèse, écrite par le code
    st.markdown("**4. En résumé**")
    st.markdown("\n\n".join(f["synthese"]))

    # Réglages : ce que XK7 a compris, modifiable, relancé en local sans repasser par l'IA
    plan = f.get("plan") or {}
    with st.expander("Réglages de la prévision", icon=":material/tune:"):
        st.caption(f"Ce que XK7 a compris : valeur « {readable(plan.get('cible')) or 'nombre de lignes'} », "
                   f"date « {readable(plan.get('date'))} »"
                   + (f", filtres : {plan['filtres']}" if plan.get("filtres") else "") +
                   ". Change un réglage puis relance : le calcul est refait en local, sans IA.")
        options = ["mediane", "moyenne", "somme", "nombre"]
        c1, c2, c3 = st.columns(3)
        agregation = c1.selectbox("Calcul par période", options, index=options.index(plan.get("agregation", "mediane")),
                                  format_func={"mediane": "Médiane", "moyenne": "Moyenne", "somme": "Somme",
                                               "nombre": "Nombre de lignes"}.get, key=f"agg_{key}")
        periodes = ["mois", "trimestre"]
        new_periode = c2.selectbox("Période", periodes, index=periodes.index(plan.get("periode", "mois")),
                                   format_func=str.capitalize, key=f"per_{key}")
        horizon = c3.slider("Horizon", 1, 12, int(plan.get("horizon", 6)), key=f"hor_{key}")
        if isinstance(key, int) and st.button("Relancer la prévision", icon=":material/refresh:", key=f"rerun_{key}"):
            new_plan = dict(plan, agregation=agregation, periode=new_periode, horizon=horizon)
            if agregation != "nombre" and not plan.get("cible"):
                st.warning("Il n'y a pas de valeur à calculer : seul « Nombre de lignes » est possible.")
            else:
                result = run_forecast_plan(new_plan, f["donnees"])
                messages = st.session_state["messages"]
                if isinstance(key, int) and key < len(messages):
                    updated = forecast_message({"type": "temps", "plan": new_plan, "donnees": f["donnees"], **result},
                                               msg["content"].split("*Base : ")[1].split(" · ")[0])
                    messages[key].update(updated)
                    st.rerun()


def analysis_message(result, db_label):
    """Transforme un résultat de XK7-ANALYSE en message à afficher (conclusion écrite par le code, sans IA)."""
    if result["type"] == "impossible":
        return {"badge": "XK7-ANALYSE", "content": f"Je ne peux pas faire cette analyse : {result['erreur']}"}
    if result["type"] == "refus":
        return {"badge": "XK7-ANALYSE", "content": "Je ne peux pas faire ça : XK7-ANALYSE est en lecture seule, il ne modifie jamais les données."}
    if result["type"] == "echec":
        return {"badge": "XK7-ANALYSE", "content": "Je n'ai pas réussi à faire cette analyse. Essaie de préciser la mesure (ventes, "
                           "chiffre d'affaires, quantités...) et le regroupement (par mois, par pays...)."}
    lines = "  \n".join(f"- {line}" for line in result["conclusion"])
    content = (f"**{result['titre']}**\n\n{lines}\n\n*Base : {db_label} · {result['lignes']} lignes analysées · "
               "calculs en local, conclusion écrite par le code*")
    return {"badge": "XK7-ANALYSE", "content": content,
            "analysis": {k: result[k] for k in ("chart", "table", "sql", "explication")}}


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
    return {"badge": "XK7-QUALITE", "content": f"{text}\n\n*Base : {db_label} · contrôles faits en local, seules les "
                       "règles métier sont proposées par le modèle à partir de la structure*", "quality": report}


def sql_message(result, db_label):
    """Transforme un résultat de XK7-SQL en message à afficher."""
    if result.get("write_sql"):                   # demande de modification : proposition, jamais exécutée
        return {"badge": "XK7-SQL", "content": f"{result['explanation']}\n\n{result['message']}",
                "write_sql": result["write_sql"], "sql": result["sql"], "columns": result["columns"],
                "rows": result["rows"], "truncated": result["truncated"]}
    if not result["ok"]:
        return {"badge": "XK7-SQL", "content": result["message"]}
    n = len(result["rows"])
    content = f"{result['explanation']}\n\n*Base : {db_label} · {n} ligne{'s' if n > 1 else ''}*"
    if result.get("warning"):
        content += f"\n\n**{result['warning']}**"
    return {"badge": "XK7-SQL", "content": content, "sql": result["sql"], "columns": result["columns"],
            "rows": result["rows"], "truncated": result["truncated"]}


def conversation_markdown():
    """La conversation en Markdown : questions, réponses et requêtes SQL (pas les tableaux de données)."""
    lines = ["# Conversation XK7", ""]
    for msg in st.session_state["messages"]:
        if msg["role"] == "user":
            lines += [f"## {msg['content']}", ""]
            continue
        for part in [msg] + msg.get("parts", []):
            if part.get("badge"):
                lines.append(f"**{part['badge']}**")
            lines += [part["content"], ""]
            sql = part.get("sql") or (part.get("analysis") or {}).get("sql")
            if sql:
                lines += ["```sql", sql, "```", ""]
    return "\n".join(lines)


def easter_egg():
    """/kalinka : la bande-son de XK7 et son tableau d'honneur (scores de toutes les évaluations)."""
    with st.chat_message("assistant", avatar=":material/music_note:"):
        st.markdown("**Kalinka, version XK7** · 8-bit, 160 ans de domaine public, arrangement maison")
        song = os.path.join("assets", "xk7_kalinka.mp3")
        if os.path.exists(song):
            st.audio(song, autoplay=True)
        st.markdown("**Tableau d'honneur**")
        for col, (label, value) in zip(st.columns(4), [("Routage", "73/73"), ("SQL", "31/31"),
                                                        ("Qualité", "21/21"), ("Analyse", "18/18")]):
            col.metric(label, value)
        st.caption("Données envoyées aux modèles pendant toutes ces évaluations : aucune.")
    st.balloons()


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
             "Données : XK7-SQL interroge la base. Qualité : XK7-QUALITE contrôle les données. "
             "Analyse : XK7-ANALYSE trace un graphique et en tire une conclusion.",
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
    if agent in ("core", "sql", "qualite", "analyse", "prevision"):
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

    if st.session_state["messages"]:
        st.download_button("Exporter la conversation", conversation_markdown().encode("utf-8"),
                           file_name="conversation_xk7.md", mime="text/markdown", width="stretch",
                           icon=":material/download:")

    st.divider()
    with st.expander("À propos"):
        st.caption(
            "XK7 regroupe des agents IA spécialisés. XK7-CORE lit la question et choisit le bon agent, ou les "
            "enchaîne. XK7-RAG répond à partir d'une bibliothèque d'ouvrages sur les bases de données, en citant "
            "ses sources. XK7-SQL interroge une base de données en écrivant lui-même les requêtes SQL. "
            "XK7-QUALITE contrôle la qualité des données et propose des règles métier. XK7-ANALYSE calcule une "
            "évolution, une répartition ou une corrélation en local et trace le graphique."
        )
        st.caption(
            "Sources : un cours universitaire de SQL ; Database Design, A. Watt et N. Eng (CC BY 4.0) ; "
            "Documentation de PostgreSQL 18, The PostgreSQL Global Development Group (licence PostgreSQL) ; "
            "Readings in Database Systems, P. Bailis, J. M. Hellerstein et M. Stonebraker (CC BY-NC-ND) ; "
            "base Chinook, L. Rocha (licence MIT)."
        )

# --- Écran d'accueil
st.title("XK7")
if agent != "rag" and db_path:
    st.caption(f":material/database: {db_label}")
pending = None
if not st.session_state["messages"]:
    st.markdown(WELCOME[agent])
    if agent == "core":
        # Une carte par agent : ce qu'il fait et un exemple à cliquer
        for row in (CARDS[:3], CARDS[3:]):
            for col, (icon, name, role, example) in zip(st.columns(3), row):
                with col.container(border=True):
                    st.markdown(f"{icon} **{name}**")
                    st.caption(role)
                    if st.button(example, key=f"card_{name}", width="stretch"):
                        pending = example
    else:
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
if agent in ("core", "sql", "qualite", "analyse", "prevision") and db_path is None:
    st.info("Dépose un fichier dans la barre latérale pour commencer.")
elif (question := st.chat_input("Pose ta question...") or pending) and question.strip().lower() == "/kalinka":
    easter_egg()
elif question:
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

    captured = []                       # tout ce qui sera envoyé aux modèles pendant cette réponse
    CAPTURE.set(captured)
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
                parts.append({"badge": "XK7-RAG", "content": reply, "sources": sources})
            if result.get("sql"):
                parts.append(sql_message(result["sql"], db_label))
            if result.get("qualite"):
                parts.append(quality_message(result["qualite"], db_label))
            if result.get("analyse"):
                parts.append(analysis_message(result["analyse"], db_label))
            if result.get("prevision"):
                parts.append(forecast_message(result["prevision"], db_label))
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
        elif agent == "analyse":
            with st.spinner("Analyse des données..."):
                result = run_analysis(question, db_path)
            new_msg = {"role": "assistant", "agent": "analyse", **analysis_message(result, db_label)}
            show_message(new_msg, len(st.session_state["messages"]))
        elif agent == "prevision":
            with st.spinner("Mise en concurrence des modèles sur le passé..."):
                result = prevoir(question, db_path)
            new_msg = {"role": "assistant", "agent": "prevision", **forecast_message(result, db_label)}
            show_message(new_msg, len(st.session_state["messages"]))
        elif agent == "rag":
            with st.spinner("Recherche dans les documents..."):
                reply, hits = answer(question, k=k, history=history, level=level)
            # On ne garde que les passages réellement cités dans la réponse
            sources = [] if REFUS in reply else keep_cited(hits, reply)
            new_msg = {"role": "assistant", "agent": "rag", "badge": "XK7-RAG", "content": reply, "sources": sources}
            show_message(new_msg, len(st.session_state["messages"]))
        else:
            with st.spinner("Écriture et exécution de la requête..."):
                result = ask(question, db_path)
            new_msg = {"role": "assistant", "agent": "sql", **sql_message(result, db_label)}
            show_message(new_msg, len(st.session_state["messages"]))

        new_msg["sent"] = list(captured)
        new_msg["agent_rag"] = new_msg.get("badge") == "XK7-RAG" or any(
            p.get("badge") == "XK7-RAG" for p in new_msg.get("parts", []))
        show_sent(new_msg)
    CAPTURE.set(None)
    st.session_state["messages"].append(new_msg)
    if pending:
        st.rerun()   # efface l'écran d'accueil après un exemple cliqué
