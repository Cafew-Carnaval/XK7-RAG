"""XK7-ANALYSE : analyse une base (évolution, comparaison, répartition, corrélation, classement) et trace un graphique.

Confidentialité niveau 2, comme XK7-SQL et XK7-QUALITE :
- le modèle ne voit que la STRUCTURE de la base et la question ; il choisit l'analyse et écrit un SELECT d'extraction ;
- les calculs sont faits en local avec pandas ;
- la conclusion est écrite par le code à partir des chiffres : aucune donnée n'est envoyée au modèle.
"""
import json
import sqlite3
import pandas as pd
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from sql_schema import get_schema, DB_PATH
from sql_agent import run_sql, UnsafeQuery

load_dotenv()

plan_llm = ChatOpenAI(model="gpt-4o", temperature=0, model_kwargs={"response_format": {"type": "json_object"}})
PLAN_LOG = []          # tout ce qui est envoyé au modèle, pour vérifier la confidentialité
LOCAL_ERRORS = []      # détail des erreurs inattendues, gardé en local (jamais envoyé au modèle)
MAX_ROWS = 200_000     # lignes extraites au maximum pour une analyse
MAX_SECONDS = 10
MAX_ATTEMPTS = 3       # 1 essai + 2 corrections
TYPES = ["evolution", "comparaison", "repartition", "correlation", "classement"]

PLAN_PROMPT = """Tu es analyste de données. Tu choisis l'analyse qui répond à la question et tu écris la requête SQLite
qui EXTRAIT les données nécessaires. Tu ne vois que la structure de la base (tables, colonnes, liens), jamais les données.
Les calculs (sommes, moyennes, regroupements) seront faits ensuite par un autre programme : ta requête renvoie des
lignes de détail, SANS GROUP BY, SANS fonction d'agrégation (SUM, AVG, COUNT) et SANS LIMIT.

Types d'analyse :
- evolution : une valeur au fil du temps ("évolution", "tendance", "par mois", "au fil des années").
  x = une colonne de date, periode = "jour", "mois" ou "annee".
- comparaison : une valeur comparée entre des catégories ("par pays", "selon le genre", "entre les villes").
  x = la colonne de catégorie.
- classement : les meilleurs ou les pires ("top 5", "les 10 plus...", "le plus vendu"). x = la catégorie, n = le nombre demandé
  (10 par défaut), ordre = "desc" pour les plus grands, "asc" pour les plus petits.
- repartition : comment se répartit un total ou un nombre ("répartition", "part de", "proportion", "distribution").
  x = la catégorie, ou une colonne numérique pour la distribution d'une valeur (ex. distribution des prix).
- correlation : lien entre deux valeurs numériques ("lien entre", "est-ce que X dépend de Y", "corrélation").
  x et y = deux colonnes numériques, agregation = "aucune".

Règles :
- Une seule requête SELECT (ou WITH ... SELECT). Jamais INSERT, UPDATE, DELETE, DROP, ALTER, CREATE.
- Noms exacts de la structure, entre guillemets doubles s'ils contiennent des espaces, des accents ou une apostrophe.
- Fais les jointures grâce aux liens indiqués par "->" ou aux colonnes de même nom.
- Donne un nom lisible en français à chaque colonne extraite avec AS (ex. AS "Pays", AS "Montant").
  Dans le plan, x et y sont ces noms lisibles.
- Une valeur calculée par ligne est permise et souvent nécessaire (ex. quantité * prix unitaire AS "Montant").
- y = la valeur numérique à agréger ; agregation = "somme", "moyenne" ou "nombre" (pour compter des lignes : y = null).
- Les dates sont des textes 'AAAA-MM-JJ HH:MM:SS'. Extrais la date telle quelle : le regroupement par période est fait après.
- Tu ne connais pas les valeurs exactes des données : pour filtrer sur un texte, utilise LIKE.
- Si la question ne demande pas une analyse de ces données, réponds {{"type": "impossible", "raison": "..."}}.

Structure de la base :
{schema}

Question : {question}
{previous_error}
Réponds uniquement avec un JSON :
{{"type": "...", "sql": "SELECT ...", "x": "...", "y": "..." ou null, "agregation": "somme|moyenne|nombre|aucune",
 "periode": "jour|mois|annee" ou null, "n": 10, "ordre": "desc", "titre": "titre court du graphique",
 "explication": "une phrase qui dit ce qui est calculé"}}"""

RETRY_TEXT = """
Ton plan précédent a échoué :
{plan}
Erreur : {error}
Corrige le plan en respectant les mêmes règles."""

AGG_WORDS = {"somme": "total", "moyenne": "moyenne", "nombre": "nombre"}


class PlanError(Exception):
    """Plan inutilisable. Le message ne contient jamais de valeur de données : il peut être renvoyé au modèle."""


# --- Mise en forme des nombres (à la française) -------------------------------------------------------

def fmt(value, decimals=None):
    if pd.isna(value):
        return "?"
    if decimals is None:
        decimals = 0 if abs(value) >= 100 or float(value).is_integer() else 2
    text = f"{value:,.{decimals}f}".replace(",", " ").replace(".", ",")
    return text


def pct(value):
    return f"{fmt(value * 100, 1)} %"


# --- 1. Le plan (seule étape qui utilise le modèle) ---------------------------------------------------

def make_plan(question, schema, previous=None, error=None):
    previous_error = RETRY_TEXT.format(plan=json.dumps(previous, ensure_ascii=False), error=error) if previous else ""
    prompt = PLAN_PROMPT.format(schema=schema, question=question, previous_error=previous_error)
    PLAN_LOG.append(prompt)
    try:
        plan = json.loads(plan_llm.invoke(prompt).content)
    except json.JSONDecodeError:
        raise PlanError("La réponse n'est pas un JSON valide.")
    return plan


def normalize_plan(plan):
    """Répare les petites maladresses fréquentes du modèle au lieu de refaire un essai."""
    if plan.get("type") in ("repartition", "comparaison", "classement") and not plan.get("x") and plan.get("y"):
        plan["x"] = plan["y"]                         # distribution d'une valeur rangée en y au lieu de x
    if plan.get("type") != "correlation" and plan.get("agregation") not in ("somme", "moyenne", "nombre"):
        plan["agregation"] = "nombre"                 # « aucune » ou mot inconnu : on compte les lignes
    if plan.get("agregation") in ("somme", "moyenne") and not plan.get("y"):
        plan["agregation"] = "nombre"
    return plan


def check_plan(plan):
    kind = plan.get("type")
    if kind == "impossible":
        return
    if kind not in TYPES:
        raise PlanError(f"Type d'analyse inconnu : {kind}. Types possibles : {', '.join(TYPES)}.")
    if not plan.get("sql") or not plan.get("x"):
        raise PlanError("Le plan doit contenir une requête (sql) et une colonne x.")
    if kind == "correlation" and not plan.get("y"):
        raise PlanError("Une corrélation demande deux colonnes numériques x et y.")
    if plan.get("agregation") in ("somme", "moyenne") and not plan.get("y"):
        raise PlanError("Une somme ou une moyenne demande une colonne y.")


# --- 2. L'extraction (en local, lecture seule) --------------------------------------------------------

def extract(plan, db_path):
    columns, rows, truncated = run_sql(plan["sql"], db_path, max_rows=MAX_ROWS, max_seconds=MAX_SECONDS)
    df = pd.DataFrame(rows, columns=columns)
    for col in [plan["x"]] + ([plan["y"]] if plan.get("y") else []):
        if col not in df.columns:
            raise PlanError(f"La colonne « {col} » n'est pas dans le résultat de la requête "
                            f"(colonnes obtenues : {', '.join(columns)}).")
    if df.empty:
        raise PlanError("La requête ne renvoie aucune ligne (filtre trop strict ?).")
    return df, truncated


def numeric(df, col):
    """Colonne convertie en nombres. Erreur (sans citer de valeur) si la plupart des valeurs ne sont pas des nombres."""
    values = pd.to_numeric(df[col], errors="coerce")
    if values.notna().mean() < 0.8:
        raise PlanError(f"La colonne « {col} » ne contient pas des nombres.")
    return values


def aggregate(df, plan, by):
    """Regroupe par `by` avec l'agrégation du plan. Renvoie une série nommée."""
    agg = plan.get("agregation") or "nombre"
    if agg == "nombre" or not plan.get("y"):
        return df.groupby(by, dropna=True).size().rename("Nombre")
    values = numeric(df, plan["y"])
    grouped = values.groupby(by, dropna=True)
    name = f"{plan['y']} ({AGG_WORDS[agg]})"
    return (grouped.mean() if agg == "moyenne" else grouped.sum()).rename(name)


# --- 3. Les calculs et la conclusion, par type d'analyse -----------------------------------------------

PERIODS = {"jour": ("D", "%d/%m/%Y"), "mois": ("M", "%m/%Y"), "annee": ("Y", "%Y")}


def analyse_evolution(df, plan):
    dates = pd.to_datetime(df[plan["x"]], errors="coerce")
    if dates.notna().mean() < 0.8:
        raise PlanError(f"La colonne « {plan['x']} » ne contient pas des dates.")
    freq, label = PERIODS.get(plan.get("periode") or "mois", PERIODS["mois"])
    periods = dates.dt.to_period(freq)
    series = aggregate(df, plan, periods).sort_index()
    series = series.reindex(pd.period_range(series.index.min(), series.index.max(), freq=freq), fill_value=0)
    table = series.rename_axis("Période").reset_index()
    table["Période"] = table["Période"].dt.strftime(label)

    first, last = series.iloc[0], series.iloc[-1]
    best, worst = series.idxmax(), series.idxmin()
    lines = [f"De {series.index[0].strftime(label)} à {series.index[-1].strftime(label)}, "
             f"« {series.name} » passe de {fmt(first)} à {fmt(last)}"
             + (f" ({'+' if last >= first else ''}{pct((last - first) / first)})." if first else ".")]
    lines.append(f"Point le plus haut : {best.strftime(label)} ({fmt(series.max())}). "
                 f"Point le plus bas : {worst.strftime(label)} ({fmt(series.min())}).")
    if len(series) >= 4:
        half = len(series) // 2
        a, b = series.iloc[:half].mean(), series.iloc[half:].mean()
        if a:
            trend = "hausse" if b > a * 1.05 else "baisse" if b < a * 0.95 else "stabilité"
            lines.append(f"Tendance générale : {trend} (moyenne de la 2e moitié de la période "
                         f"{'+' if b >= a else ''}{pct((b - a) / a)} par rapport à la 1re).")
    # Dernière période incomplète (ex. un mois dont les données s'arrêtent le 5) : la comparaison serait trompeuse
    # (on ne le signale que s'il manque plus de 10 % de la période : 9 jours sur une année, ce n'est rien)
    last_period = series.index[-1]
    covered = (dates.max() - last_period.start_time) / (last_period.end_time - last_period.start_time)
    if covered < 0.9 and len(series) > 2:
        lines.append(f"Attention : la dernière période ({series.index[-1].strftime(label)}) semble incomplète "
                     f"(données jusqu'au {dates.max().strftime('%d/%m/%Y')}), sa valeur est donc sous-estimée.")
    # Pour le graphique : de vraies dates (l'axe choisit lui-même combien d'étiquettes afficher)
    timeline = pd.Series(series.values, index=series.index.to_timestamp(), name=series.name).rename_axis("Période")
    chart = {"kind": "line", "data": timeline, "format": label,
             "metrics": [{"label": f"Début ({series.index[0].strftime(label)})", "value": fmt(first)},
                         {"label": f"Fin ({series.index[-1].strftime(label)})", "value": fmt(last),
                          "delta": f"{'+' if last >= first else ''}{pct((last - first) / first)}" if first else None},
                         {"label": f"Plus haut ({best.strftime(label)})", "value": fmt(series.max())}]}
    return table, chart, lines


def analyse_comparaison(df, plan, top=None):
    series = aggregate(df, plan, df[plan["x"]].astype("string").str.strip())
    ascending = plan.get("ordre") == "asc"
    series = series.sort_values(ascending=ascending)
    total = series.sum()
    shown = series.head(top) if top else series
    if not top and len(series) > 15:                       # trop de catégories : on garde les 15 premières
        shown = series.head(15)
    table = shown.rename_axis(plan["x"]).reset_index()

    lines = []
    if top:
        lines.append(f"{'Les' if len(shown) > 1 else 'Le'} {len(shown)} {'premiers' if not ascending else 'derniers'} "
                     f"sur {len(series)} (colonne « {plan['x']} »).")
    lines.append(f"En tête : {shown.index[0]} ({fmt(shown.iloc[0])})"
                 + (f", puis {shown.index[1]} ({fmt(shown.iloc[1])})." if len(shown) > 1 else "."))
    agg = plan.get("agregation") or "nombre"
    if agg != "moyenne" and total and not ascending:
        lines.append(f"{shown.index[0]} représente {pct(shown.iloc[0] / total)} du total"
                     + (f", les 3 premiers {pct(series.head(3).sum() / total)}." if len(series) > 3 else "."))
    if len(series) > 1 and not top:
        low = series.sort_values().index[0]
        lines.append(f"Le plus faible : {low} ({fmt(series[low])}), soit "
                     f"{fmt(series.max() / series[low], 1) if series[low] else '∞'} fois moins que le premier."
                     if series[low] > 0 else f"Le plus faible : {low} ({fmt(series[low])}).")
    if len(series) > len(shown):
        lines.append(f"Seules les {len(shown)} premières catégories sur {len(series)} sont affichées.")
    chart = {"kind": "bar", "data": shown,
             "metrics": [{"label": "En tête", "value": str(shown.index[0]), "delta": fmt(shown.iloc[0]),
                          "delta_color": "off"},
                         {"label": "Catégories", "value": str(len(series))}]}
    if agg != "moyenne" and total and not ascending:
        chart["metrics"].append({"label": "Part du premier", "value": pct(shown.iloc[0] / total)})
    return table, chart, lines


def analyse_classement(df, plan):
    n = int(plan.get("n") or 10)
    return analyse_comparaison(df, plan, top=max(1, min(n, 50)))


def analyse_repartition(df, plan):
    x = plan["x"]
    as_numbers = pd.to_numeric(df[x], errors="coerce")
    if as_numbers.notna().mean() >= 0.8 and df[x].nunique() > 15:
        # Distribution d'une valeur numérique : histogramme en 10 tranches
        values = as_numbers.dropna()
        bins = pd.cut(values, bins=10)
        counts = bins.value_counts().sort_index()
        table = pd.DataFrame({"Tranche": [f"{fmt(i.left)} à {fmt(i.right)}" for i in counts.index],
                              "Nombre": counts.values})
        lines = [f"{len(values)} valeurs, de {fmt(values.min())} à {fmt(values.max())}.",
                 f"Moyenne : {fmt(values.mean())}, médiane : {fmt(values.median())} "
                 f"(la moitié des valeurs est en dessous)."]
        top_bin = table.loc[table["Nombre"].idxmax()]
        lines.append(f"La tranche la plus fréquente est {top_bin['Tranche']} "
                     f"({pct(top_bin['Nombre'] / len(values))} des valeurs).")
        if values.mean() > values.median() * 1.2:
            lines.append("La moyenne est nettement au-dessus de la médiane : quelques valeurs élevées tirent la moyenne.")
        chart = {"kind": "bar", "data": table.set_index("Tranche")["Nombre"],
                 "metrics": [{"label": "Valeurs", "value": fmt(len(values))},
                             {"label": "Moyenne", "value": fmt(values.mean())},
                             {"label": "Médiane", "value": fmt(values.median())}]}
        return table, chart, lines

    series = aggregate(df, plan, df[x].astype("string").str.strip()).sort_values(ascending=False)
    shares = series / series.sum()
    table = pd.DataFrame({x: series.index, series.name: series.values, "Part": [pct(s) for s in shares]})
    lines = [f"{len(series)} valeurs différentes de « {x} »."]
    lines.append(f"La plus grande part : {series.index[0]} avec {pct(shares.iloc[0])}"
                 + (f", puis {series.index[1]} ({pct(shares.iloc[1])})." if len(series) > 1 else "."))
    if len(series) > 3:
        lines.append(f"Les 3 premiers font {pct(shares.head(3).sum())} du total.")
    if shares.iloc[0] > 0.5:
        lines.append("Une seule catégorie fait plus de la moitié du total : la répartition est très concentrée.")
    chart = {"kind": "bar", "data": (shares.head(15) * 100).rename("Part (%)"),
             "metrics": [{"label": "Plus grande part", "value": str(series.index[0]), "delta": pct(shares.iloc[0]),
                          "delta_color": "off"},
                         {"label": "Catégories", "value": str(len(series))}]}
    if len(series) > 3:
        chart["metrics"].append({"label": "Part des 3 premiers", "value": pct(shares.head(3).sum())})
    return table, chart, lines


def strength(r):
    a = abs(r)
    word = "très forte" if a >= 0.8 else "forte" if a >= 0.6 else "moyenne" if a >= 0.4 else "faible" if a >= 0.2 else "quasi nulle"
    sense = "" if a < 0.2 else (" et positive (quand l'une augmente, l'autre aussi)" if r > 0
                                else " et négative (quand l'une augmente, l'autre diminue)")
    return word + sense


def analyse_correlation(df, plan):
    x, y = plan["x"], plan["y"]
    data = pd.DataFrame({x: numeric(df, x), y: numeric(df, y)}).dropna()
    if len(data) < 3:
        raise PlanError("Pas assez de lignes avec deux nombres pour calculer une corrélation.")
    r = data[x].corr(data[y])
    rho = data[x].rank().corr(data[y].rank())          # Spearman = corrélation des rangs (sans scipy)
    lines = [f"Corrélation entre {x} et {y} sur {len(data)} lignes : r = {fmt(r, 2)}, liaison {strength(r)}."]
    if abs(rho - r) > 0.2:
        lines.append(f"Corrélation de rang (Spearman) : {fmt(rho, 2)}. L'écart avec r montre un lien qui n'est pas "
                     f"une ligne droite, ou quelques valeurs extrêmes.")
    lines.append("Une corrélation ne prouve pas que l'une des valeurs est la cause de l'autre.")
    table = data.describe().loc[["mean", "min", "max"]].rename(index={"mean": "Moyenne", "min": "Min", "max": "Max"})
    sample = data.sample(min(len(data), 2000), random_state=0)
    chart = {"kind": "scatter", "data": sample, "x": x, "y": y, "r": r,
             "metrics": [{"label": "Corrélation (r)", "value": fmt(r, 2), "delta": strength(r).split(" et ")[0],
                          "delta_color": "off"},
                         {"label": "Lignes", "value": fmt(len(data))}]}
    return table.reset_index(names="Mesure"), chart, lines


ANALYSES = {"evolution": analyse_evolution, "comparaison": analyse_comparaison, "classement": analyse_classement,
            "repartition": analyse_repartition, "correlation": analyse_correlation}


# --- 4. L'agent complet ---------------------------------------------------------------------------------

def run_plan(plan, db_path):
    """Exécute un plan : extraction, calculs, conclusion. Entièrement local."""
    check_plan(normalize_plan(plan))
    df, truncated = extract(plan, db_path)
    table, chart, lines = ANALYSES[plan["type"]](df, plan)
    raw = table.copy()                                 # chiffres bruts, pour l'évaluation
    table = table.apply(lambda col: col.map(fmt) if pd.api.types.is_numeric_dtype(col) else col)   # nombres lisibles
    chart["title"] = plan.get("titre") or ""
    if truncated:
        lines.append(f"Attention : seules les {fmt(MAX_ROWS)} premières lignes ont été analysées.")
    return {"table": table, "raw": raw, "chart": chart, "conclusion": lines, "lignes": len(df)}


def analyse(question, db_path=DB_PATH):
    """Répond à une question d'analyse. Renvoie type, titre, sql, explication, table, chart, conclusion
    (ou erreur). Le modèle ne reçoit que la structure, la question et ses propres plans ratés."""
    schema = get_schema(db_path)
    plan, error = None, None
    for attempt in range(MAX_ATTEMPTS):
        try:
            plan = make_plan(question, schema, plan, error)
            if plan.get("type") == "impossible":
                return {"type": "impossible", "erreur": plan.get("raison") or "Ce n'est pas une question d'analyse.",
                        "plan": plan, "essais": attempt + 1}
            result = run_plan(plan, db_path)
            return {"type": plan["type"], "titre": plan.get("titre", ""), "sql": plan["sql"],
                    "explication": plan.get("explication", ""), "plan": plan, "essais": attempt + 1, **result}
        except UnsafeQuery as e:                      # sécurité : refus définitif, pas de nouvel essai
            return {"type": "refus", "erreur": str(e), "plan": plan, "essais": attempt + 1}
        except (PlanError, sqlite3.Error) as e:
            error = str(e)                             # erreur sans donnée : on la renvoie au modèle
        except (KeyError, ValueError, TypeError) as e:
            # Message générique (pas de valeur de données) ; le détail reste en local pour le débogage
            error = f"Plan inutilisable ({type(e).__name__}). Vérifie le type, x, y et l'agrégation."
            LOCAL_ERRORS.append(repr(e))
    return {"type": "echec", "erreur": error, "plan": plan, "essais": MAX_ATTEMPTS}


def show(result):
    print(f"[{result['type']}] {result.get('titre', '')}  ({result['essais']} essai(s))")
    if "erreur" in result:
        print("  Erreur :", result["erreur"])
        return
    print("  SQL :", " ".join(result["sql"].split()))
    print(result["table"].head(10).to_string(index=False))
    for line in result["conclusion"]:
        print("  •", line)


if __name__ == "__main__":
    import sys
    db = sys.argv[1] if len(sys.argv) > 1 else DB_PATH
    for q in ["Comment évolue le chiffre d'affaires par année ?",
              "Quels sont les 5 pays qui rapportent le plus ?",
              "Quelle est la répartition des morceaux par genre ?",
              "Y a-t-il un lien entre la durée d'un morceau et sa taille en octets ?"]:
        print("\n" + q)
        show(analyse(q, db))
