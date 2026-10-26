"""XK7-PREVISION : prévoit l'évolution d'une valeur dans le temps (partie 1 : mode « dans le temps »).

Même principe que XK7-ANALYSE : l'IA externe choisit, le code local calcule.
- GPT ne voit que la STRUCTURE de la base et la question : il dit quoi prévoir (cible, date, période, horizon) et
  écrit le SELECT d'extraction ;
- tout le reste est local : la série, les 6 modèles (scikit-learn), le backtest, la prévision et la synthèse.
Aucune donnée n'est envoyée au modèle de langage.
"""
import json
import re
import sqlite3
import warnings
import numpy as np
import pandas as pd
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from sklearn.ensemble import HistGradientBoostingRegressor, RandomForestRegressor
from sklearn.linear_model import LinearRegression
from sklearn.model_selection import GridSearchCV, TimeSeriesSplit
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.svm import SVR
from sql_schema import get_schema, DB_PATH
from sql_agent import run_sql, UnsafeQuery

load_dotenv()
warnings.filterwarnings("ignore", category=UserWarning)

plan_llm = ChatOpenAI(model="gpt-4o", temperature=0, model_kwargs={"response_format": {"type": "json_object"}})
PLAN_LOG = []            # tout ce qui est envoyé au modèle, pour vérifier la confidentialité
MAX_ROWS = 200_000
MAX_SECONDS = 10
MAX_ATTEMPTS = 3

# Historique minimum, par type de période : (pour prévoir une tendance, pour prendre en compte la saisonnalité).
# Mois : 1 an pour une tendance, 2 ans pour voir chaque mois au moins deux fois. Trimestres : 2 ans, puis 3 ans.
MIN_HISTORY = {"mois": (12, 24), "trimestre": (8, 12)}
BACKTEST = 6             # nombre de périodes rejouées pour juger les modèles (12 si l'historique est long)
PERIODS = {"mois": ("M", "%m/%Y", 12), "trimestre": ("Q", "T%q %Y", 4)}


class PlanError(Exception):
    """Plan inutilisable. Le message ne contient jamais de valeur de données : il peut être renvoyé au modèle."""


def fmt(value, decimals=None):
    if value is None or pd.isna(value):
        return "?"
    if decimals is None:
        decimals = 0 if abs(value) >= 10 or float(value).is_integer() else 2
    return f"{value:,.{decimals}f}".replace(",", " ").replace(".", ",")


def pct(value, decimals=1):
    return f"{fmt(value * 100, decimals)} %"


def label(period, periode):
    """Étiquette lisible d'une période : 03/2025, ou T1 2025 pour un trimestre."""
    if periode == "trimestre":
        return f"T{period.quarter} {period.year}"
    return period.strftime(PERIODS[periode][1])


# =====================================================================================================
# 1. La série : une valeur par période (ex. le prix médian au m² de chaque mois)
# =====================================================================================================

def build_series(df, date_col, target_col, agregation="mediane", periode="mois"):
    dates = pd.to_datetime(df[date_col], errors="coerce")
    if dates.notna().mean() < 0.8:
        raise PlanError(f"La colonne « {date_col} » ne contient pas des dates.")
    freq = PERIODS[periode][0]
    if agregation == "nombre":
        values = pd.Series(1, index=df.index)
    else:
        values = pd.to_numeric(df[target_col], errors="coerce")
        if values.notna().mean() < 0.8:
            raise PlanError(f"La colonne « {target_col} » ne contient pas des nombres.")
    grouped = values.groupby(dates.dt.to_period(freq))
    series = {"mediane": grouped.median, "moyenne": grouped.mean, "somme": grouped.sum,
              "nombre": grouped.sum}[agregation]().sort_index()
    # Périodes sans aucune ligne : 0 pour un total ou un nombre, valeur précédente pour un prix médian ou moyen
    full = pd.period_range(series.index.min(), series.index.max(), freq=freq)
    series = series.reindex(full)
    series = series.fillna(0) if agregation in ("somme", "nombre") else series.ffill()
    # Dernière période incomplète (ex. un mois dont les données s'arrêtent le 5) : on la retire, sinon les
    # modèles apprendraient une fausse chute
    last = series.index[-1]
    covered = (dates.max() - last.start_time) / (last.end_time - last.start_time)
    if covered < 0.9:
        series = series.iloc[:-1]
    return series


def history_level(series, periode):
    """Garde-fou : assez d'historique pour prévoir ? « aucune », « tendance » ou « complete »."""
    minimum, seasonal = MIN_HISTORY[periode]
    if len(series) < minimum:
        return "aucune"
    return "complete" if len(series) >= seasonal else "tendance"


# =====================================================================================================
# 2. Les variables : le passé transformé en colonnes (les « lags »), le mois, le temps écoulé
# =====================================================================================================

def features(values, t, season, seasonal):
    """Variables pour prédire la période t à partir des valeurs connues avant t (values[:t]).
    Les modèles prédisent la VARIATION par rapport à la dernière valeur, pas la valeur elle-même :
    un arbre ne sait pas prédire plus haut que ce qu'il a vu ; une variation, si (c'est ce qui lui permet de
    suivre une tendance)."""
    last = values[t - 1]
    row = {"variation_1": values[t - 1] - values[t - 2],      # comment ça a bougé la dernière fois
           "variation_2": values[t - 2] - values[t - 3],
           "ecart_moyenne_3": last - np.mean(values[t - 3:t]),  # au-dessus ou en dessous de la tendance récente
           "temps": t}                                         # le temps écoulé : la tendance longue
    if seasonal:
        row["saison_n-1"] = values[t - season] - values[t - season - 1]   # même mois l'an dernier
        angle = 2 * np.pi * (t % season) / season                         # le mois, sur un cercle :
        row["saison_sin"], row["saison_cos"] = np.sin(angle), np.cos(angle)   # décembre est voisin de janvier
    return row, last


def training_set(values, end, season, seasonal):
    """Toutes les lignes d'entraînement disponibles avant `end` : X (variables), y (variation à prédire)."""
    start = season + 1 if seasonal else 3
    rows, targets = [], []
    for t in range(start, end):
        row, last = features(values, t, season, seasonal)
        rows.append(row)
        targets.append(values[t] - last)
    return pd.DataFrame(rows), np.array(targets)


# =====================================================================================================
# 3. Les 6 modèles en compétition
# =====================================================================================================

def svr_model():
    """SVR : mise à l'échelle obligatoire (elle mesure des distances), réglages C et epsilon choisis par une
    validation croisée qui respecte l'ordre du temps (TimeSeriesSplit : on n'apprend jamais avec le futur)."""
    return GridSearchCV(make_pipeline(StandardScaler(), SVR(kernel="rbf")),
                        {"svr__C": [0.1, 1, 10, 100], "svr__epsilon": [0.01, 0.1, 0.5]},
                        cv=TimeSeriesSplit(n_splits=3), scoring="neg_mean_absolute_error")


ML_MODELS = {
    "Régression linéaire": lambda: make_pipeline(StandardScaler(), LinearRegression()),
    "SVR": svr_model,
    "Forêt aléatoire": lambda: RandomForestRegressor(n_estimators=200, min_samples_leaf=2, random_state=0),
    "Gradient boosting": lambda: HistGradientBoostingRegressor(max_iter=150, learning_rate=0.05, max_depth=3,
                                                             min_samples_leaf=5, random_state=0),
}
MODELS = ["Naïf", "Moyenne mobile (3)"] + list(ML_MODELS)
SEASONAL_NAIVE = "Naïf saisonnier"
ADJUSTED_NAIVE = "Naïf désaisonnalisé"


def seasonal_factors(known, season):
    """Coefficient de chaque période de l'année (ex. 0,88 pour un 1er trimestre 12 % sous la tendance), calculé
    UNIQUEMENT sur les valeurs connues : rapport à la moyenne mobile sur un an, moyenné par période, ramené à 1."""
    s = pd.Series(known, dtype=float)
    trend = s.rolling(season, center=True, min_periods=season).mean()
    ratio = (s / trend).replace([np.inf, -np.inf], np.nan)
    slots = pd.Series(np.arange(len(s)) % season)
    factors = ratio.groupby(slots).mean().reindex(range(season)).fillna(1.0)
    return (factors / factors.mean()).to_numpy()


def models_for(seasonal):
    """Les modèles en compétition. Avec au moins 2 ans d'historique, on ajoute le naïf saisonnier : « le prochain
    3e trimestre sera comme le dernier 3e trimestre ». Aussi simple que le naïf, mais il connaît la saisonnalité,
    là où les modèles de ML n'ont parfois pas assez d'exemples pour l'apprendre."""
    return MODELS[:2] + ([SEASONAL_NAIVE, ADJUSTED_NAIVE] if seasonal else []) + MODELS[2:]


def predict_next(name, values, t, season, seasonal, fitted=None):
    """Prédit la période t à partir des valeurs connues avant t."""
    if name == "Naïf":
        return values[t - 1]                                   # demain = aujourd'hui
    if name == "Moyenne mobile (3)":
        return float(np.mean(values[t - 3:t]))                 # moyenne des 3 dernières périodes
    if name == SEASONAL_NAIVE:
        return values[t - season]                              # comme la même période, un an avant
    if name == ADJUSTED_NAIVE:                                 # le niveau du naïf, l'effet saisonnier en plus
        f = seasonal_factors(values[:t], season)
        before, after = f[(t - 1) % season], f[t % season]
        return values[t - 1] * after / before if before > 0 else values[t - 1]
    model = fitted
    if model is None:
        X, y = training_set(values, t, season, seasonal)
        model = ML_MODELS[name]().fit(X, y)
    row, last = features(values, t, season, seasonal)
    return last + float(model.predict(pd.DataFrame([row]))[0])


# =====================================================================================================
# 4. Le backtest : on se replace dans le passé et on compare les prédictions à ce qui s'est vraiment passé
# =====================================================================================================

def backtest_length(values, season):
    """6 périodes par défaut ; une année entière si l'historique est long (moins de hasard dans le classement)."""
    return max(BACKTEST, season) if len(values) >= 3 * season else BACKTEST


def metrics(actual, predicted, naive):
    """MAPE et MAE (mises en avant), RMSE, et le gain sur le naïf : de combien le modèle réduit l'erreur du naïf.
    Pas de R² ici : sur quelques périodes presque plates, il compare les modèles à la moyenne des vraies valeurs,
    qu'aucun modèle ne pouvait connaître à l'avance, et il est presque toujours négatif, donc trompeur."""
    actual, predicted, naive = (np.array(a, float) for a in (actual, predicted, naive))
    errors = predicted - actual
    nonzero = actual != 0
    mape = float(np.mean(np.abs(errors[nonzero]) / np.abs(actual[nonzero]))) if nonzero.any() else np.nan
    naive_mae = np.mean(np.abs(naive - actual))
    mae = float(np.mean(np.abs(errors)))
    return {"MAPE": mape, "MAE": mae, "RMSE": float(np.sqrt(np.mean(errors ** 2))),
            "Gain sur le naïf": float(1 - mae / naive_mae) if naive_mae > 0 else np.nan}


def backtest(values, season, seasonal, n):
    """Pour chacune des n dernières périodes : entraînement sur tout ce qui précède, prédiction de cette période.
    Jamais de mélange aléatoire : un modèle qui aurait vu le futur aurait l'air bien meilleur qu'il ne l'est."""
    preds = {name: [predict_next(name, values, t, season, seasonal) for t in range(len(values) - n, len(values))]
             for name in models_for(seasonal)}
    return {name: {"predictions": p, **metrics(values[-n:], p, preds["Naïf"])} for name, p in preds.items()}


def choose(results):
    """Le gagnant : la plus petite MAPE (la MAE si la série passe par zéro). Il doit battre nettement le naïf."""
    key = "MAPE" if all(not np.isnan(r["MAPE"]) for r in results.values()) else "MAE"
    ranking = sorted(results, key=lambda m: results[m][key])
    best = ranking[0]
    naive = results["Naïf"][key]
    beats_naive = best != "Naïf" and results[best][key] < 0.95 * naive
    return best, ranking, key, beats_naive


# =====================================================================================================
# 5. La prévision : le gagnant, réentraîné sur tout l'historique, prédit période après période
# =====================================================================================================

def forecast(name, values, horizon, season, seasonal):
    """Prévision « récursive » : chaque période prédite devient une valeur connue pour prédire la suivante."""
    extended = list(values)
    fitted = None
    if name in ML_MODELS:
        X, y = training_set(np.array(extended), len(extended), season, seasonal)
        fitted = ML_MODELS[name]().fit(X, y)
    for _ in range(horizon):
        t = len(extended)
        extended.append(predict_next(name, np.array(extended), t, season, seasonal, fitted))
    return extended[len(values):]


def interval(values_forecast, backtest_preds, actual):
    """Fourchette à 80 % tirée des erreurs du backtest : si le modèle s'est trompé de 3 % au plus 8 fois sur 10,
    on prend ± 3 %, élargi avec l'horizon (plus on regarde loin, moins on est sûr : facteur racine de h)."""
    actual = np.array(actual, float)
    rel = np.abs(np.array(backtest_preds) - actual) / np.where(actual == 0, np.nan, np.abs(actual))
    width = float(np.nanquantile(rel, 0.8)) if np.isfinite(rel).any() else 0.1
    width = max(width, 0.005)
    low = [v * (1 - width * np.sqrt(h + 1)) for h, v in enumerate(values_forecast)]
    high = [v * (1 + width * np.sqrt(h + 1)) for h, v in enumerate(values_forecast)]
    return low, high, width


# =====================================================================================================
# 6. Le contexte et la synthèse, écrits par le code (le modèle de langage ne voit aucun chiffre)
# =====================================================================================================

def seasonality(series, periode):
    """Période de l'année la plus haute et la plus basse, en écart moyen à la moyenne mobile sur un an."""
    season = PERIODS[periode][2]
    trend = series.rolling(season, center=True, min_periods=season // 2).mean()
    ratio = (series / trend).dropna()
    by_slot = ratio.groupby(ratio.index.month if periode == "mois" else ratio.index.quarter).mean()
    if by_slot.empty or by_slot.max() - by_slot.min() < 0.02:
        return None
    names = (["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre",
              "novembre", "décembre"] if periode == "mois" else ["le 1er trimestre", "le 2e trimestre", "le 3e trimestre",
                                                                   "le 4e trimestre"])
    return (names[by_slot.idxmax() - 1], by_slot.max() - 1, names[by_slot.idxmin() - 1], by_slot.min() - 1)


def context_lines(series, periode, target_name):
    first, last = series.iloc[0], series.iloc[-1]
    peak, low = series.idxmax(), series.idxmin()
    lines = [f"De {label(series.index[0], periode)} à {label(series.index[-1], periode)}, « {target_name} » passe de "
             f"{fmt(first)} à {fmt(last)} ({'+' if last >= first else ''}{pct((last - first) / first) if first else '?'})."]
    if peak not in (series.index[0], series.index[-1]):
        lines.append(f"Point le plus haut : {label(peak, periode)} ({fmt(series.max())}), soit "
                     f"{pct((last - series.max()) / series.max())} entre ce sommet et la dernière période.")
    season = PERIODS[periode][2]
    if len(series) > season:
        recent = series.iloc[-1] / series.iloc[-season - 1] - 1
        lines.append(f"Sur la dernière année : {'+' if recent >= 0 else ''}{pct(recent)}.")
    return lines


def confidence(mape, level, beats_naive):
    if level == "tendance":
        base = "à prendre avec prudence : moins de 2 ans d'historique, la saisonnalité n'est pas prise en compte"
    else:
        base = None
    if np.isnan(mape) or mape > 0.15:
        word = "faible"
    elif mape > 0.05:
        word = "moyenne"
    else:
        word = "bonne"
    return word, base


def synthesis(series, periode, target_name, best, results, key, beats_naive, fc, low, high, level, season_info):
    used = best if beats_naive else "Naïf"
    r = results[used]
    unit_err = f"{pct(r['MAPE'])}, soit environ {fmt(r['MAE'])}" if key == "MAPE" else f"{fmt(r['MAE'])}"
    word, caution = confidence(r["MAPE"], level, beats_naive)
    parts = [" ".join(context_lines(series, periode, target_name))]
    if season_info:
        hi, hi_v, lo, lo_v = season_info
        parts.append(f"Saisonnalité : {hi} est en général le plus haut ({'+' if hi_v >= 0 else ''}{pct(hi_v)} par rapport "
                     f"à la tendance), {lo} le plus bas ({pct(lo_v)}).")
    n = len(r["predictions"])
    count = {6: "Six", 7: "Sept", 8: "Huit"}.get(len(results), str(len(results)))
    intro = (f"{count} modèles ont été mis en concurrence sur les {n} dernières périodes, comme si on était dans le passé. ")
    if beats_naive:
        parts.append(intro + f"Le meilleur est « {used} » : il s'est trompé en moyenne de {unit_err} par période, "
                     f"{pct(r['Gain sur le naïf'], 0)} d'erreur en moins que le simple « demain = aujourd'hui ».")
    else:
        gain = results[best]["Gain sur le naïf"]
        parts.append(intro + (f"Le meilleur, « {best} », ne fait pas nettement mieux que le simple « demain = aujourd'hui » "
                              f"({pct(gain, 0)} d'erreur en moins seulement)" if best != "Naïf" else
                              "Aucun modèle ne fait mieux que le simple « demain = aujourd'hui »") +
                     (" : la saisonnalité existe, mais sur les dernières périodes les changements de niveau ont pesé "
                        "plus lourd qu'elle, et les modèles qui en tiennent compte se sont trompés davantage" if season_info
                        else " : la série est trop irrégulière pour dégager une tendance fiable")
                     + f". La prévision retenue est donc la dernière valeur connue, avec l'erreur du naïf : {unit_err} "
                     f"par période en moyenne."
                     + (" Conseil : relance la prévision par mois (dans les réglages) : l'historique y est plus long, "
                        "ce qui laisse plus de chances aux modèles saisonniers." if periode == "trimestre" else ""))
    parts.append(f"Prévision : {fmt(fc[0])} pour {label(series.index[-1] + 1, periode)} (entre {fmt(low[0])} et "
                 f"{fmt(high[0])})" + (f", puis {fmt(fc[-1])} pour {label(series.index[-1] + len(fc), periode)} "
                                        f"(entre {fmt(low[-1])} et {fmt(high[-1])})." if len(fc) > 1 else "."))
    parts.append(f"Fiabilité : {word}" + (f", {caution}." if caution else ".") +
                 " Une prévision prolonge le passé : elle ne voit pas venir un choc (taux, crise, nouvelle loi).")
    return parts


# =====================================================================================================
# 7. L'agent complet sur des données déjà extraites (entièrement local)
# =====================================================================================================

def run_time_forecast(df, date_col, target_col, agregation="mediane", periode="mois", horizon=6,
                      target_name=None):
    target_name = target_name or (target_col if agregation != "nombre" else "Nombre")
    series = build_series(df, date_col, target_col, agregation, periode)
    level = history_level(series, periode)
    season = PERIODS[periode][2]
    out = {"serie": series, "niveau": level, "periode": periode, "cible": target_name,
           "contexte": context_lines(series, periode, target_name), "distribution": None}
    if agregation != "nombre":
        out["distribution"] = pd.to_numeric(df[target_col], errors="coerce").dropna()
    if level == "aucune":
        out["synthese"] = out["contexte"] + [
            f"Pas de prévision : il faut au moins {MIN_HISTORY[periode][0]} périodes d'historique, le fichier en "
            f"compte {len(series)}. Voici l'évolution observée."]
        return out
    seasonal = level == "complete"
    values = series.to_numpy(float)
    n_test = backtest_length(values, season)
    results = backtest(values, season, seasonal, n_test)
    best, ranking, key, beats_naive = choose(results)
    used = best if beats_naive else "Naïf"
    horizon = int(max(1, min(horizon, season)))
    fc = forecast(used, values, horizon, season, seasonal)
    low, high, width = interval(fc, results[used]["predictions"], values[-n_test:])
    if values.min() >= 0:                       # un chiffre d'affaires, un prix, un nombre ne sont jamais négatifs
        low, fc = [max(0.0, v) for v in low], [max(0.0, v) for v in fc]
    season_info = seasonality(series, periode) if seasonal else None
    future = pd.period_range(series.index[-1] + 1, periods=horizon, freq=series.index.freq)
    out.update({
        "modeles": pd.DataFrame([{"Modèle": m, **{k: results[m][k] for k in ("MAPE", "MAE", "RMSE", "Gain sur le naïf")}}
                                 for m in ranking]),
        "gagnant": best, "utilise": used, "bat_le_naif": beats_naive, "critere": key,
        "backtest": pd.DataFrame({"Période": [label(p, periode) for p in series.index[-n_test:]],
                                  "Réel": values[-n_test:], **{m: results[m]["predictions"] for m in results}}),
        "prevision": pd.DataFrame({"Période": future, "Prévision": fc, "Bas": low, "Haut": high}),
        "synthese": synthesis(series, periode, target_name, best, results, key, beats_naive, fc, low, high, level,
                              season_info),
    })
    return out


# =====================================================================================================
# 8. Le plan, fait par GPT à partir de la STRUCTURE seule, puis l'agent complet
# =====================================================================================================

PLAN_PROMPT = """Tu es analyste de données. L'utilisateur veut une PRÉVISION. Tu choisis quoi prévoir et tu écris la requête
SQLite qui EXTRAIT les données nécessaires. Tu ne vois que la structure de la base, jamais les données. Les calculs
(regroupement par période, modèles de prévision) seront faits ensuite par un autre programme.

Deux modes :
- "temps" : prévoir l'évolution d'une valeur dans le temps ("comment va évoluer le prix", "prévois les ventes des
  6 prochains mois", "combien de commandes le mois prochain ?").
- "caracteristiques" : estimer une valeur à partir de caractéristiques ("combien vaut un T3 de 65 m² à Rezé ?",
  "quel prix pour une maison de 120 m² avec jardin ?").
Si la question ne demande pas une prévision ou une estimation sur ces données, mode = "impossible".

Pour le mode "temps" :
- date : la colonne de date ; cible : la colonne numérique à prévoir (null pour compter des lignes).
- agregation : "mediane" pour un prix ou un montant unitaire (prix au m², prix d'un bien, panier), "somme" pour un total
  (chiffre d'affaires, quantités vendues), "nombre" pour compter des lignes (nombre de ventes, de commandes),
  "moyenne" seulement si la question dit "moyen".
- periode : "mois" par défaut, "trimestre" si la question le demande.
- horizon : le nombre de périodes à prévoir (6 mois par défaut, 4 trimestres par défaut, 12 au maximum).
- La requête renvoie des lignes de détail, SANS GROUP BY, SANS SUM, AVG ou COUNT, SANS LIMIT, avec les filtres de
  la question (type de bien, ville...). Une valeur calculée par ligne est permise (ex. quantité * prix AS "Montant").
- Donne un nom lisible en français à chaque colonne avec AS ; date et cible sont ces noms lisibles.

Règles SQL : une seule requête SELECT (ou WITH ... SELECT) ; noms exacts de la structure, entre guillemets doubles
s'ils contiennent des espaces, des accents ou une apostrophe ; jointures par les liens "->" ou les colonnes de même
nom ; pour filtrer sur un texte dont tu ne connais pas l'orthographe exacte, utilise LIKE.

Structure de la base :
{schema}

Question : {question}
{previous_error}
Réponds uniquement avec un JSON :
{{"mode": "temps" | "caracteristiques" | "impossible", "sql": "SELECT ...", "date": "...", "cible": "..." ou null,
 "agregation": "mediane|moyenne|somme|nombre", "periode": "mois|trimestre", "horizon": 6,
 "titre": "titre court", "filtres": "les filtres en quelques mots (ex. appartements, Nantes) ou vide",
 "ligne": "ce que représente une ligne de la requête, au singulier (ex. vente, commande, facture)",
 "raison": "seulement si impossible"}}"""

RETRY_TEXT = """
Ton plan précédent a échoué :
{plan}
Erreur : {error}
Corrige le plan en respectant les mêmes règles."""


def make_plan(question, schema, previous=None, error=None):
    previous_error = RETRY_TEXT.format(plan=json.dumps(previous, ensure_ascii=False), error=error) if previous else ""
    prompt = PLAN_PROMPT.format(schema=schema, question=question, previous_error=previous_error)
    PLAN_LOG.append(prompt)
    try:
        return json.loads(plan_llm.invoke(prompt).content)
    except json.JSONDecodeError:
        raise PlanError("La réponse n'est pas un JSON valide.")


def normalize_plan(plan):
    """Répare les petites maladresses du modèle et vérifie le plan (messages sans aucune valeur de données)."""
    if plan.get("mode") != "temps":
        return plan
    if plan.get("agregation") not in ("mediane", "moyenne", "somme", "nombre"):
        plan["agregation"] = "nombre" if not plan.get("cible") else "mediane"
    if not plan.get("cible"):
        plan["agregation"] = "nombre"
    if plan.get("periode") not in PERIODS:
        plan["periode"] = "mois"
    try:
        plan["horizon"] = int(plan.get("horizon") or 0)
    except (TypeError, ValueError):
        plan["horizon"] = 0
    plan["horizon"] = min(12, plan["horizon"]) if plan["horizon"] > 0 else (6 if plan["periode"] == "mois" else 4)
    if not plan.get("sql") or not plan.get("date"):
        raise PlanError("Le plan doit contenir une requête (sql) et une colonne de date.")
    return plan


def extract(plan, db_path):
    columns, rows, truncated = run_sql(plan["sql"], db_path, max_rows=MAX_ROWS, max_seconds=MAX_SECONDS)
    df = pd.DataFrame(rows, columns=columns)
    for key in ("date", "cible"):
        col = plan.get(key)
        if col and col not in df.columns:
            raise PlanError(f"La colonne « {col} » n'est pas dans le résultat de la requête "
                            f"(colonnes obtenues : {', '.join(columns)}).")
    if df.empty:
        raise PlanError("La requête ne renvoie aucune ligne (filtre trop strict ?).")
    return df, truncated


def readable(name):
    """Nom de colonne lisible, même si le modèle a écrit « prix_au_m2 » malgré la consigne : « Prix au m² »."""
    text = re.sub(r"_+", " ", str(name or "")).strip()
    text = re.sub(r"\bm2\b", "m²", text)
    return text[:1].upper() + text[1:] if text else text


def run_plan(plan, df):
    """Prévision à partir d'un plan et de données déjà extraites : entièrement local. C'est aussi ce que l'app
    relance quand l'utilisateur change un réglage (agrégation, période, horizon), sans repasser par GPT."""
    plan = normalize_plan(dict(plan))
    target = plan.get("cible") or plan["date"]
    line = str(plan.get("ligne") or "").strip()
    name = readable(plan.get("cible")) or (f"Nombre de {line}s" if line and not line.endswith("s") else
                                           f"Nombre de {line}" if line else "Nombre de lignes")
    if plan.get("filtres"):
        name = f"{name} ({plan['filtres']})"
    return run_time_forecast(df, plan["date"], target, plan["agregation"], plan["periode"], plan["horizon"], name)


def prevoir(question, db_path=DB_PATH):
    """Point d'entrée de XK7-PREVISION. Le modèle ne reçoit que la structure, la question et ses plans ratés."""
    schema = get_schema(db_path)
    plan, error = None, None
    for attempt in range(MAX_ATTEMPTS):
        try:
            plan = make_plan(question, schema, plan, error)
            mode = plan.get("mode")
            if mode == "impossible":
                return {"type": "impossible", "erreur": plan.get("raison") or "Ce n'est pas une demande de prévision.",
                        "plan": plan, "essais": attempt + 1}
            if mode == "caracteristiques":
                return {"type": "bientot", "erreur": "L'estimation selon des caractéristiques arrive bientôt : pour "
                        "l'instant, XK7-PREVISION prévoit l'évolution d'une valeur dans le temps.",
                        "plan": plan, "essais": attempt + 1}
            plan = normalize_plan(plan)
            df, truncated = extract(plan, db_path)
            result = run_plan(plan, df)
            if truncated:
                result["synthese"].append(f"Attention : seules les {fmt(MAX_ROWS)} premières lignes ont été utilisées.")
            return {"type": "temps", "plan": plan, "donnees": df, "essais": attempt + 1, **result}
        except UnsafeQuery as e:
            return {"type": "refus", "erreur": str(e), "plan": plan, "essais": attempt + 1}
        except (PlanError, sqlite3.Error) as e:
            error = str(e)
        except (KeyError, ValueError, TypeError) as e:
            error = f"Plan inutilisable ({type(e).__name__}). Vérifie le mode, la date, la cible et l'agrégation."
    return {"type": "echec", "erreur": error, "plan": plan, "essais": MAX_ATTEMPTS}


if __name__ == "__main__":
    import sys, time
    from data_upload import to_sqlite
    path = sys.argv[1] if len(sys.argv) > 1 else "data/ventes_immobilier_44.xlsx"
    db = to_sqlite(path.split("/")[-1], open(path, "rb").read())
    cols, rows, _ = run_sql('SELECT "Date de vente", "Prix au m² (€)" FROM Ventes WHERE "Type de bien" = '
                            "'Appartement'", db, max_rows=MAX_ROWS)
    start = time.time()
    res = run_time_forecast(pd.DataFrame(rows, columns=cols), "Date de vente", "Prix au m² (€)", "mediane", "mois", 6,
                            "Prix médian au m² des appartements")
    print(f"({time.time() - start:.1f} s, historique : {res['niveau']})\n")
    print(res["modeles"].to_string(index=False, formatters={"MAPE": pct, "MAE": fmt, "RMSE": fmt,
                                                            "Gain sur le naïf": lambda v: pct(v, 0)}))
    print()
    for p in res["synthese"]:
        print("•", p)
