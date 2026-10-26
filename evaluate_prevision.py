"""Évalue XK7-PREVISION.
1. Tests locaux (sans GPT, reproductibles) : pas de triche dans le backtest, reproductibilité, cohérence, garde-fous.
2. Tests avec GPT : bon mode choisi, valeurs de la question comprises, aucune donnée envoyée au modèle.
Usage : python evaluate_prevision.py            (tout)
        python evaluate_prevision.py --local    (seulement les tests locaux, sans appel à l'API)
"""
import re
import sys
import numpy as np
import pandas as pd
import prevision_agent as pa
from estimation import run_estimation, re_estimate, split
from sql_agent import run_sql
from data_upload import to_sqlite, delete_file

DVF_FILE, CAFE_FILE, CHINOOK = "data/ventes_immobilier_44.xlsx", "data/ventes_cafe_test.xlsx", "data/chinook.db"
results = []


def check(name, ok, detail=""):
    results.append(ok)
    print(f"{'OK ' if ok else 'ERR'} {name}" + (f"  ({detail})" if detail else ""))


def rows(sql, db):
    cols, data, _ = run_sql(sql, db, max_rows=pa.MAX_ROWS, max_seconds=30)
    return pd.DataFrame(data, columns=cols)


def local_tests(dvf, cafe):
    print("=== 1. Tests locaux (sans GPT)\n")
    prices = rows('SELECT "Date de vente" AS d, "Prix au m² (€)" AS p FROM Ventes WHERE "Type de bien" = '
                  "'Appartement' AND Commune LIKE 'Nantes'", dvf)
    series = pa.build_series(prices, "d", "p", "mediane", "mois")
    values = series.to_numpy(float)

    # Pas de triche : changer le futur ne doit rien changer aux prédictions du passé
    t = len(values) - 6
    future_changed = values.copy()
    future_changed[t:] = future_changed[t:] * 3 + 1000
    for name in pa.models_for(True):
        a = pa.predict_next(name, values, t, 12, True)
        b = pa.predict_next(name, future_changed, t, 12, True)
        check(f"Backtest sans triche : {name}", abs(a - b) < 1e-9, f"{a:.1f} et {b:.1f}")

    # Reproductibilité
    r1 = pa.run_time_forecast(prices, "d", "p", "mediane", "mois", 6, "Prix")
    r2 = pa.run_time_forecast(prices, "d", "p", "mediane", "mois", 6, "Prix")
    check("Prévision reproductible", r1["utilise"] == r2["utilise"]
          and np.allclose(r1["prevision"]["Prévision"], r2["prevision"]["Prévision"]),
          f"{r1['utilise']}, {r1['prevision']['Prévision'].iloc[0]:.0f}")
    p = r1["prevision"]
    check("Prévision dans sa fourchette", bool(((p["Bas"] <= p["Prévision"]) & (p["Prévision"] <= p["Haut"])).all()))
    check("Fourchette qui s'élargit avec l'horizon",
          bool(((p["Haut"] - p["Bas"]).diff().dropna() >= -1e-9).all()))

    # Garde-fou d'historique : 7 mois de café, pas de prévision
    sales = rows('SELECT c."Date commande" AS d, c."Quantité" * p."Prix unitaire" AS m FROM Commandes c JOIN Produits p '
                 'ON p."Réf produit" = c."Réf produit"', cafe)
    short = pa.run_time_forecast(sales[sales["d"] < "2024-09-01"], "d", "m", "somme", "mois", 3, "CA")
    check("Historique trop court : pas de prévision", short["niveau"] == "aucune" and "prevision" not in short)

    # Mode caractéristiques
    data = rows('SELECT "Date de vente", "Type de bien", Commune, "Surface (m²)", "Pièces", "Terrain (m²)", Neuf, '
                'Latitude, Longitude, "Prix au m² (€)", "Prix (€)" FROM Ventes', dvf)
    variables = ["Type de bien", "Commune", "Surface (m²)", "Pièces", "Terrain (m²)", "Neuf", "Latitude", "Longitude",
                 "Prix au m² (€)"]
    given = {"Type de bien": "Appartement", "Surface (m²)": 65, "Pièces": 3, "Commune": "Rezé"}
    e1 = run_estimation(data, "Prix (€)", variables, given, "Date de vente", "Prix")
    e2 = run_estimation(data, "Prix (€)", variables, given, "Date de vente", "Prix")
    check("Estimation reproductible", abs(e1["estimation"] - e2["estimation"]) < 1e-6, f"{e1['estimation']:.0f} €")
    check("Fuite de données : prix au m² retiré, surface gardée",
          "Prix au m² (€)" in e1["retirees"] and "Surface (m²)" not in e1["retirees"], ", ".join(e1["retirees"]))
    check("Estimation dans sa fourchette", e1["bas"] <= e1["estimation"] <= e1["haut"])
    small, medium, large = (re_estimate(e1, {**e1["fiche"], "Surface (m²)": s})[0] for s in (40, 65, 100))
    check("Cohérence : 40 m² < 65 m² < 100 m²", small < medium < large,
          f"{small:.0f} < {medium:.0f} < {large:.0f}")
    check("Le meilleur modèle bat la médiane des biens semblables",
          e1["gagnant"] != "Médiane des biens semblables" and e1["gain"] > 0, f"{e1['gagnant']}, -{e1['gain']:.0%}")
    train, test, _ = split(data, "Date de vente")
    check("Test sur des ventes postérieures à l'entraînement",
          pd.to_datetime(train["Date de vente"]).max() < pd.to_datetime(test["Date de vente"]).min())
    few = run_estimation(data.head(150), "Prix (€)", variables, given, "Date de vente")
    check("Trop peu de lignes : pas d'estimation", "erreur" in few)
    typo = run_estimation(data, "Prix (€)", variables, {**given, "Commune": "Rezzé"}, "Date de vente")
    check("Faute de frappe comprise (Rezzé -> Rezé)", "erreur" not in typo and typo["fiche"]["Commune"] == "Rezé")


QUESTIONS = [   # (base, question, mode attendu, valeur numérique qui doit être comprise)
    ("dvf", "Comment va évoluer le prix au m² des appartements à Nantes ?", "temps", None),
    ("dvf", "Combien de ventes le mois prochain ?", "temps", None),
    ("dvf", "Combien vaut une maison de 120 m² à Vertou ?", "caracteristiques", 120),
    ("dvf", "Estime le prix d'un T2 de 45 m² à Nantes.", "caracteristiques", 45),
    ("chinook", "Prévois le chiffre d'affaires des 6 prochains mois.", "temps", None),
    ("chinook", "Combien vaut un appartement de 50 m² à Paris ?", "impossible", None),
]


def gpt_tests(bases):
    print("\n=== 2. Tests avec GPT (mode choisi, valeurs comprises, confidentialité)\n")
    communes = set(rows("SELECT DISTINCT Commune FROM Ventes", bases["dvf"])["Commune"].astype(str))
    for base, question, expected, number in QUESTIONS:
        pa.PLAN_LOG.clear()
        result = pa.prevoir(question, bases[base])
        kind = result["type"]
        ok = kind == expected
        detail = kind
        if ok and number is not None:
            values = [v for v in (result.get("fiche") or {}).values() if isinstance(v, (int, float))]
            ok = any(abs(v - number) < 1e-6 for v in values)
            detail += f", {number} compris" if ok else f", {number} NON compris"
        if ok and kind in ("temps", "caracteristiques"):
            detail += f", gagnant : {result.get('utilise') or result.get('gagnant')}"
        check(question, ok, detail)
        if not ok and kind in ("echec", "impossible"):
            print(f"       erreur : {result.get('erreur')}")
            print(f"       plan : {result.get('plan')}")
            if pa.LOCAL_ERRORS:
                print(f"       détail local : {pa.LOCAL_ERRORS[-1]}")
        # Confidentialité : aucune commune autre que celles de la question, aucun montant
        sent = "\n".join(pa.PLAN_LOG)
        leaked = [c for c in communes if len(c) > 4 and c not in question and re.search(rf"\b{re.escape(c)}\b", sent)]
        check("   aucune donnée envoyée au modèle", not leaked, ", ".join(leaked[:5]))


if __name__ == "__main__":
    bases = {"dvf": to_sqlite("ventes_immobilier_44.xlsx", open(DVF_FILE, "rb").read()),
             "cafe": to_sqlite("ventes_cafe_test.xlsx", open(CAFE_FILE, "rb").read()), "chinook": CHINOOK}
    try:
        local_tests(bases["dvf"], bases["cafe"])
        if "--local" not in sys.argv:
            gpt_tests(bases)
    finally:
        delete_file(bases["dvf"])
        delete_file(bases["cafe"])
    print(f"\nRésultat : {sum(results)}/{len(results)}")
