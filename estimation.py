"""XK7-PREVISION, mode « selon les caractéristiques » : estimer une valeur (ex. le prix d'un T3 de 65 m² à Rezé)
à partir de biens semblables. Entièrement local : scikit-learn s'entraîne sur les données, rien ne part au modèle de
langage (il a seulement choisi la cible et les variables à partir de la structure).

Décisions, à savoir expliquer :
- valeurs non données par l'utilisateur : celles des biens semblables (médiane / plus fréquente), affichées et modifiables ;
- fuite de données : une variable presque identique à la cible (le prix au m² pour estimer un prix) est retirée ;
- découpage dans le temps : entraînement sur le passé, test sur les ventes les plus récentes ;
- apprentissage sur le logarithme de la cible : les modèles raisonnent en pourcentage, comme la MAPE.
"""
import difflib
import time
import unicodedata
import warnings
import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingRegressor, RandomForestRegressor
from sklearn.impute import SimpleImputer
from sklearn.inspection import permutation_importance
from sklearn.linear_model import Ridge
from sklearn.model_selection import train_test_split
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import OneHotEncoder, OrdinalEncoder, StandardScaler
from sklearn.svm import SVR

warnings.filterwarnings("ignore", category=UserWarning)

MIN_ROWS = 200             # en dessous : pas assez d'exemples pour apprendre
SVR_SAMPLE = 4000          # la SVR est lente sur beaucoup de lignes : on l'entraîne sur un échantillon
FOREST_SAMPLE = 20000
TEST_MONTHS = 6
LEAK_CORR = 0.98           # une variable aussi liée à la cible la contient déjà (fuite de données)


def fmt(value, decimals=None):
    if value is None or pd.isna(value):
        return "?"
    if decimals is None:
        decimals = 0 if abs(value) >= 10 or float(value).is_integer() else 2
    return f"{value:,.{decimals}f}".replace(",", " ").replace(".", ",")


def pct(value, decimals=1):
    return f"{fmt(value * 100, decimals)} %"


def plain(text):
    return unicodedata.normalize("NFKD", str(text)).encode("ascii", "ignore").decode().lower().strip()


# =====================================================================================================
# 1. Préparer les données : types de variables, fuite de données
# =====================================================================================================

def column_kinds(df, variables):
    """Numériques ou catégories (texte). Une colonne de nombres avec très peu de valeurs reste numérique."""
    numeric, categorical = [], []
    for v in variables:
        present = df[v].dropna()                       # une colonne souvent vide (le terrain d'un appartement)
        values = pd.to_numeric(present, errors="coerce")  # reste numérique : on regarde les valeurs présentes
        (numeric if len(present) and values.notna().mean() >= 0.9 else categorical).append(v)
    return numeric, categorical


def leaking(df, target, numeric):
    """Variables qui contiennent déjà la réponse (fuite de données) :
    - une variable presque identique à la cible ;
    - un couple dont le produit redonne la cible (prix au m² × surface = prix) : on ne retire que celui des deux
      qui, seul, explique le moins la cible (le prix au m², pas la surface, qui est une vraie caractéristique)."""
    y = pd.to_numeric(df[target], errors="coerce")
    cols = {v: pd.to_numeric(df[v], errors="coerce") for v in numeric}

    def corr(a, b):
        pair = pd.concat([a, b], axis=1).dropna()
        return abs(pair.iloc[:, 0].corr(pair.iloc[:, 1])) if len(pair) > 10 else 0.0

    found = {v for v in numeric if corr(cols[v], y) >= LEAK_CORR}
    for i, a in enumerate(numeric):
        for b in numeric[i + 1:]:
            if a in found or b in found:
                continue
            if corr(cols[a] * cols[b], y) >= LEAK_CORR:
                found.add(a if corr(cols[a], y) < corr(cols[b], y) else b)
    return [v for v in numeric if v in found]


def split(df, date_col):
    """Test = les ventes des 6 derniers mois s'il y a une date (prédire le futur), sinon 20 % au hasard."""
    if date_col and date_col in df.columns:
        dates = pd.to_datetime(df[date_col], errors="coerce")
        cutoff = dates.max() - pd.DateOffset(months=TEST_MONTHS)
        test = dates > cutoff
        if 0.05 <= test.mean() <= 0.5:
            return df[~test], df[test], f"entraînement sur les données jusqu'au {cutoff:%d/%m/%Y}, test sur les " \
                                        f"{TEST_MONTHS} derniers mois (prédire des cas récents, jamais vus)"
    train, test = train_test_split(df, test_size=0.2, random_state=0)
    return train, test, "entraînement sur 80 % des lignes, test sur les 20 % restants tirés au hasard"


# =====================================================================================================
# 2. Les modèles en compétition
# =====================================================================================================

def preprocessor(numeric, categorical, scale):
    """Nombres : valeurs manquantes remplacées par la médiane (et mise à l'échelle pour la régression et la SVR).
    Catégories : une colonne 0/1 par catégorie fréquente (au moins 20 lignes), les rares sont regroupées."""
    num = make_pipeline(SimpleImputer(strategy="median"), *( [StandardScaler()] if scale else []))
    cat = make_pipeline(SimpleImputer(strategy="most_frequent"),
                        OneHotEncoder(handle_unknown="infrequent_if_exist", min_frequency=20, sparse_output=False))
    return ColumnTransformer([("nombres", num, numeric), ("categories", cat, categorical)])


def tree_preprocessor(numeric, categorical):
    """Pour les arbres : un numéro par catégorie au lieu d'une colonne par catégorie (aussi précis, bien plus rapide)."""
    cat = make_pipeline(SimpleImputer(strategy="most_frequent"),
                        OrdinalEncoder(handle_unknown="use_encoded_value", unknown_value=-1))
    return ColumnTransformer([("nombres", SimpleImputer(strategy="median"), numeric), ("categories", cat, categorical)])


def candidates(numeric, categorical, n_categories=None):
    # Le gradient boosting traite nativement les catégories (jusqu'à 250 valeurs différentes par colonne)
    native = [len(numeric) + i for i, c in enumerate(categorical) if (n_categories or {}).get(c, 0) <= 250]
    return {
        "Régression linéaire": (make_pipeline(preprocessor(numeric, categorical, True), Ridge(alpha=1.0)), None),
        "SVR": (make_pipeline(preprocessor(numeric, categorical, True), SVR(C=3, epsilon=0.05)), SVR_SAMPLE),
        "Forêt aléatoire": (make_pipeline(tree_preprocessor(numeric, categorical),
                                          RandomForestRegressor(n_estimators=80, min_samples_leaf=3, n_jobs=-1,
                                                                random_state=0)), FOREST_SAMPLE),
        "Gradient boosting": (make_pipeline(tree_preprocessor(numeric, categorical),
                                            HistGradientBoostingRegressor(max_iter=200, learning_rate=0.1,
                                                                          categorical_features=native or None,
                                                                          random_state=0)), None),
    }


class GroupMedian:
    """Le « naïf » du mode caractéristiques : la médiane des biens qui ont les mêmes catégories (même type, même
    commune...), ou la médiane générale s'il n'y en a pas. Aucun apprentissage : la référence à battre."""

    def __init__(self, categorical):
        self.categorical = categorical

    def fit(self, X, y):
        self.overall = float(np.median(y))
        self.groups = pd.Series(y, index=X.index).groupby([X[c] for c in self.categorical]).median() \
            if self.categorical else None
        return self

    def predict(self, X):
        if self.groups is None:
            return np.full(len(X), self.overall)
        keys = pd.MultiIndex.from_frame(X[self.categorical]) if len(self.categorical) > 1 else X[self.categorical[0]]
        return self.groups.reindex(keys).fillna(self.overall).to_numpy()


def metrics(actual, predicted):
    actual, predicted = np.asarray(actual, float), np.asarray(predicted, float)
    err = predicted - actual
    nonzero = actual != 0
    ss_tot = np.sum((actual - actual.mean()) ** 2)
    return {"MAPE": float(np.mean(np.abs(err[nonzero]) / np.abs(actual[nonzero]))),
            "MAE": float(np.mean(np.abs(err))), "RMSE": float(np.sqrt(np.mean(err ** 2))),
            "R²": float(1 - np.sum(err ** 2) / ss_tot) if ss_tot > 0 else np.nan}


# =====================================================================================================
# 3. La fiche du bien : valeurs données par l'utilisateur, valeurs typiques pour le reste
# =====================================================================================================

def match_category(value, available):
    """Retrouve la catégorie voulue malgré les majuscules et les accents (« reze » -> « Rezé »)."""
    by_plain = {plain(a): a for a in available}
    if plain(value) in by_plain:
        return by_plain[plain(value)], None
    close = difflib.get_close_matches(plain(value), list(by_plain), n=3, cutoff=0.75)
    return None, [by_plain[c] for c in close]


def build_profile(df, numeric, categorical, given):
    """Valeurs du bien à estimer. Retourne (fiche, problèmes). Les valeurs absentes de la question prennent la
    valeur typique des lignes qui partagent les catégories données (ex. la latitude médiane des ventes à Rezé)."""
    profile, sources, problems, notes = {}, {}, [], []
    similar = df
    for c in categorical:
        if c in given and str(given[c]).strip():
            found, close = match_category(given[c], df[c].dropna().astype(str).unique())
            if found is None and close and len(close) == 1:      # une seule valeur proche : on la prend et on le dit
                found = close[0]
                notes.append(f"« {given[c]} » compris comme « {found} ».")
            if found is None:
                options = df[c].dropna().astype(str).unique()
                if len(options) <= 20:
                    # Peu de catégories (ex. Appartement / Maison) : on ignore la valeur et on le dit
                    notes.append(f"« {given[c]} » n'est pas une valeur de « {c} » (valeurs possibles : "
                                 f"{', '.join(sorted(options))}) : la valeur typique des cas semblables est utilisée.")
                    continue
                # Beaucoup de catégories (ex. les communes) : estimer sans la bonne valeur n'aurait pas de sens
                problems.append(f"« {given[c]} » est introuvable dans la colonne « {c} »"
                                + (f" (valeurs proches : {', '.join(close)})." if close else "."))
                continue
            profile[c], sources[c] = found, "question"
            narrower = similar[similar[c].astype(str) == found]
            similar = narrower if len(narrower) >= 5 else similar
    for v in numeric:
        if v in given and given[v] not in (None, ""):
            try:
                profile[v], sources[v] = float(str(given[v]).replace(",", ".")), "question"
                continue
            except ValueError:
                # Ex. « avec jardin » traduit en ">0" pour le terrain : on garde la valeur typique et on le dit
                notes.append(f"« {given[v]} » n'est pas un nombre pour « {v} » : la valeur typique des cas semblables "
                             "est utilisée, modifie-la dans le simulateur si besoin.")
        values = pd.to_numeric(similar[v], errors="coerce").dropna()
        profile[v], sources[v] = (float(values.median()) if len(values) else np.nan), "typique"
    for c in categorical:
        if c not in profile:
            mode = similar[c].dropna().astype(str).mode()
            profile[c], sources[c] = (mode.iloc[0] if len(mode) else None), "typique"
    return profile, sources, problems, notes


# =====================================================================================================
# 4. L'estimation complète
# =====================================================================================================

def run_estimation(df, target, variables, given, date_col=None, target_name=None):
    """Compétition des modèles sur les données, puis estimation du bien décrit par `given` (et sa fourchette)."""
    start = time.time()
    target_name = target_name or target
    df = df.copy()
    df[target] = pd.to_numeric(df[target], errors="coerce")
    df = df[df[target].notna()]
    variables = [v for v in variables if v in df.columns and v != target and v != date_col]
    if len(df) < MIN_ROWS:
        return {"erreur": f"Pas d'estimation : il faut au moins {MIN_ROWS} lignes pour apprendre, la requête en "
                          f"renvoie {len(df)}."}
    numeric, categorical = column_kinds(df, variables)
    removed = leaking(df, target, numeric)
    numeric = [v for v in numeric if v not in removed]
    for v in numeric:
        df[v] = pd.to_numeric(df[v], errors="coerce")
    for c in categorical:
        df[c] = df[c].astype("string").fillna("(vide)").astype(str)
    if not numeric and not categorical:
        return {"erreur": "Aucune variable utilisable pour estimer la cible."}

    profile, sources, problems, notes = build_profile(df, numeric, categorical, given or {})
    if problems:
        return {"erreur": " ".join(problems)}

    train, test, split_text = split(df, date_col)
    X_cols = numeric + categorical
    log_target = bool((df[target] > 0).all())                # apprendre sur log(cible) si elle est toujours positive
    transform = (np.log if log_target else (lambda v: v))
    inverse = (np.exp if log_target else (lambda v: v))

    results, fitted = {}, {}
    naive = GroupMedian(categorical).fit(train[X_cols], train[target].to_numpy())
    results["Médiane des biens semblables"] = metrics(test[target], naive.predict(test[X_cols]))
    fitted["Médiane des biens semblables"] = naive
    n_categories = {c: df[c].nunique() for c in categorical}
    for name, (model, sample) in candidates(numeric, categorical, n_categories).items():
        part = train.sample(min(sample, len(train)), random_state=0) if sample else train
        model.fit(part[X_cols], transform(part[target].to_numpy()))
        predictions = inverse(model.predict(test[X_cols]))
        results[name], fitted[name] = metrics(test[target], predictions), model
        results[name]["_pred"] = predictions
    results["Médiane des biens semblables"]["_pred"] = naive.predict(test[X_cols])

    ranking = sorted(results, key=lambda m: results[m]["MAPE"])
    best = ranking[0]
    naive_mape = results["Médiane des biens semblables"]["MAPE"]
    gain = 1 - results[best]["MAPE"] / naive_mape if naive_mape else 0

    # Le modèle utilisé est exactement celui qui a été évalué (pas de réentraînement) : les scores affichés
    # sont ceux du modèle qui fait l'estimation
    final = fitted[best]
    predict = final.predict if best == "Médiane des biens semblables" else (lambda X: inverse(final.predict(X)))
    row = pd.DataFrame([profile])[X_cols]
    estimate = float(predict(row)[0])

    # Fourchette à 80 % : erreurs relatives du gagnant sur le test (10e et 90e centiles)
    ratio = results[best]["_pred"] / test[target].to_numpy()
    low_r, high_r = np.nanquantile(ratio, 0.1), np.nanquantile(ratio, 0.9)
    low, high = estimate / high_r, estimate / low_r

    # Ce qui compte le plus : importance par permutation (on mélange une variable et on mesure l'erreur ajoutée)
    importance = None
    if best != "Médiane des biens semblables":
        sample = test.sample(min(1500, len(test)), random_state=0)
        imp = permutation_importance(final, sample[X_cols], transform(sample[target].to_numpy()), n_repeats=3,
                                     random_state=0, scoring="neg_mean_absolute_error")
        importance = pd.Series(np.maximum(imp.importances_mean, 0), index=X_cols)
        importance = (importance / importance.sum()).sort_values(ascending=False) if importance.sum() > 0 else None

    similar = df
    for c in categorical:
        if sources.get(c) == "question":
            narrower = similar[similar[c] == profile[c]]
            similar = narrower if len(narrower) >= 5 else similar

    table = pd.DataFrame([{"Modèle": m, **{k: results[m][k] for k in ("MAPE", "MAE", "RMSE", "R²")}}
                          for m in ranking])
    out = {"estimation": estimate, "bas": low, "haut": high, "fiche": profile, "sources": sources,
           "numeriques": numeric, "categories": categorical, "retirees": removed, "modeles": table,
           "gagnant": best, "gain": gain, "importance": importance, "decoupage": split_text,
           "lignes": len(df), "semblables": similar[target], "cible": target_name, "variables": X_cols,
           "duree": time.time() - start, "_predict": predict,
           # Choix proposés dans le simulateur pour chaque catégorie (les plus fréquentes d'abord)
           "options": {c: df[c].value_counts().index[:300].tolist() for c in categorical}}
    out["synthese"] = notes + estimation_synthesis(out, results[best], target_name)
    return out


def show_value(v):
    """Une valeur de la fiche : 65, 47,21 (latitude), Appartement."""
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return fmt(v, 0 if float(v).is_integer() or abs(v) >= 1000 else 2)
    return str(v)


def describe(profile, sources, given_only):
    items = [f"{k} : {show_value(v)}" for k, v in profile.items()
             if (sources.get(k) == "question") == given_only and v is not None and not (isinstance(v, float) and np.isnan(v))]
    return ", ".join(items)


def estimation_synthesis(out, best_metrics, target_name):
    parts = []
    given = describe(out["fiche"], out["sources"], True)
    typical = describe(out["fiche"], out["sources"], False)
    parts.append(f"Estimation de « {target_name} » pour : {given or 'un cas typique'}. "
                 + (f"Valeurs typiques des cas semblables utilisées pour le reste ({typical}) : modifie-les dans la "
                    "fiche si besoin." if typical else ""))
    sim = out["semblables"]
    parts.append(f"Parmi les {fmt(len(sim))} cas les plus proches dans les données, la valeur médiane est de "
                 f"{fmt(sim.median())} (la moitié entre {fmt(sim.quantile(0.25))} et {fmt(sim.quantile(0.75))}).")
    if out["retirees"]:
        several = len(out["retirees"]) > 1
        parts.append(f"{'Variables retirées' if several else 'Variable retirée'} : {', '.join(out['retirees'])}. "
                     f"{'Elles contiennent' if several else 'Elle contient'} déjà la réponse (fuite de données) : un "
                     "modèle qui s'en servirait aurait l'air parfait mais serait inutilisable sur un nouveau cas.")
    n_models = len(out["modeles"])
    best = out["gagnant"]
    if best == "Médiane des biens semblables":
        parts.append(f"{n_models} modèles ont été comparés ({out['decoupage']}). Aucun ne fait mieux que la simple "
                     "médiane des biens semblables : c'est elle qui est retenue.")
    else:
        parts.append(f"{n_models} modèles ont été comparés ({out['decoupage']}). Le meilleur est « {best} » : "
                     f"{pct(best_metrics['MAPE'])} d'erreur en moyenne, soit environ {fmt(best_metrics['MAE'])}, "
                     f"{pct(out['gain'], 0)} d'erreur en moins que la médiane des biens semblables. R² = "
                     f"{fmt(best_metrics['R²'], 2)} : le modèle explique {pct(max(best_metrics['R²'], 0), 0)} des "
                     "écarts entre les cas.")
    if out["importance"] is not None:
        top = out["importance"].head(3)
        parts.append("Ce qui pèse le plus dans l'estimation : "
                     + ", ".join(f"{k} ({pct(v, 0)})" for k, v in top.items()) + ".")
    parts.append(f"Estimation : {fmt(out['estimation'])}, avec 8 chances sur 10 d'être entre {fmt(out['bas'])} et "
                 f"{fmt(out['haut'])}.")
    word = "bonne" if best_metrics["MAPE"] <= 0.1 else "moyenne" if best_metrics["MAPE"] <= 0.25 else "faible"
    parts.append(f"Fiabilité : {word}. Le modèle ne connaît que les colonnes du fichier : l'état, l'étage ou "
                 "l'exposition, absents des données, peuvent faire varier fortement la valeur réelle.")
    return parts


def re_estimate(out, profile):
    """Le simulateur : nouvelle estimation pour une fiche modifiée, avec le modèle déjà entraîné (instantané)."""
    row = pd.DataFrame([profile])[out["variables"]]
    for v in out["numeriques"]:
        row[v] = pd.to_numeric(row[v], errors="coerce")
    estimate = float(out["_predict"](row)[0])
    ratio_low, ratio_high = out["estimation"] / out["haut"], out["estimation"] / out["bas"]
    return estimate, estimate / ratio_high if ratio_high else np.nan, estimate / ratio_low if ratio_low else np.nan


if __name__ == "__main__":
    import sys
    sys.path.insert(0, ".")
    from data_upload import to_sqlite
    from sql_agent import run_sql
    path = sys.argv[1] if len(sys.argv) > 1 else "data/ventes_immobilier_44.xlsx"
    db = to_sqlite(path.split("/")[-1], open(path, "rb").read())
    cols, rows, _ = run_sql('SELECT "Date de vente", "Type de bien", Commune, "Surface (m²)", "Pièces", '
                            '"Terrain (m²)", Neuf, Latitude, Longitude, "Prix au m² (€)", "Prix (€)" FROM Ventes', db,
                            max_rows=200_000, max_seconds=20)
    df = pd.DataFrame(rows, columns=cols)
    res = run_estimation(df, "Prix (€)", ["Type de bien", "Commune", "Surface (m²)", "Pièces", "Terrain (m²)", "Neuf",
                                          "Latitude", "Longitude", "Prix au m² (€)"],
                         {"Type de bien": "Appartement", "Surface (m²)": 65, "Pièces": 3, "Commune": "reze"},
                         "Date de vente", "Prix")
    if "erreur" in res:
        print(res["erreur"])
    else:
        print(f"({res['duree']:.1f} s)")
        print(res["modeles"].to_string(index=False, formatters={"MAPE": pct, "MAE": fmt, "RMSE": fmt,
                                                                "R²": lambda v: fmt(v, 2)}))
        for p in res["synthese"]:
            print("•", p)
        for surface in (40, 65, 100):
            est = re_estimate(res, {**res["fiche"], "Surface (m²)": surface})
            print(f"  simulateur : {surface} m² -> {fmt(est[0])} (entre {fmt(est[1])} et {fmt(est[2])})")
