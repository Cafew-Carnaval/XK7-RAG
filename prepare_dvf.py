"""Prépare un extrait propre des ventes DVF (data.gouv.fr, Licence Ouverte 2.0) pour XK7-PREVISION.

Nettoyage classique de DVF :
- ventes simples uniquement (Vente, et Vente en l'état futur d'achèvement = logement neuf) ;
- une vente = un seul logement (maison ou appartement). Les ventes de plusieurs logements à la fois ont un prix
  global impossible à répartir : on les retire. Les dépendances (cave, garage) sont ignorées ;
- surfaces et prix plausibles, puis prix au m² entre le 1er et le 99e centile de chaque type de bien.
Usage : python prepare_dvf.py sortie.xlsx 2021.csv.gz 2022.csv.gz ...
"""
import sys
import pandas as pd

KEEP = {"Vente": "Non", "Vente en l'état futur d'achèvement": "Oui"}


def clean(path):
    df = pd.read_csv(path, low_memory=False, dtype={"code_postal": str, "code_commune": str})
    df = df[df["nature_mutation"].isin(KEEP)]
    logements = df[df["type_local"].isin(["Maison", "Appartement"])]
    # Une seule ligne de logement par vente, et un seul prix
    per_sale = logements.groupby("id_mutation").agg(n=("type_local", "size"), prix=("valeur_fonciere", "nunique"))
    simple = per_sale[(per_sale["n"] == 1) & (per_sale["prix"] == 1)].index
    sales = logements[logements["id_mutation"].isin(simple)].copy()
    # Surface du terrain : la somme des parcelles de la vente
    land = df.groupby("id_mutation")["surface_terrain"].sum()
    sales["terrain"] = sales["id_mutation"].map(land)
    out = pd.DataFrame({
        "Date de vente": pd.to_datetime(sales["date_mutation"]),
        "Commune": sales["nom_commune"], "Code postal": sales["code_postal"],
        "Type de bien": sales["type_local"], "Neuf": sales["nature_mutation"].map(KEEP),
        "Surface (m²)": sales["surface_reelle_bati"], "Pièces": sales["nombre_pieces_principales"],
        "Terrain (m²)": sales["terrain"].where(sales["type_local"] == "Maison"),
        "Prix (€)": sales["valeur_fonciere"],
        "Latitude": sales["latitude"].round(5), "Longitude": sales["longitude"].round(5)})
    out = out[(out["Surface (m²)"] >= 9) & (out["Prix (€)"] >= 10_000) & (out["Pièces"] >= 1)]
    out["Prix au m² (€)"] = (out["Prix (€)"] / out["Surface (m²)"]).round(0)
    return out


if __name__ == "__main__":
    target, sources = sys.argv[1], sys.argv[2:]
    data = pd.concat([clean(p) for p in sources], ignore_index=True)
    before = len(data)
    # Prix au m² aberrants (erreurs de saisie, ventes entre proches) : hors du 1er-99e centile de chaque type
    bounds = data.groupby("Type de bien")["Prix au m² (€)"].quantile([0.01, 0.99]).unstack()
    low, high = data["Type de bien"].map(bounds[0.01]), data["Type de bien"].map(bounds[0.99])
    data = data[(data["Prix au m² (€)"] >= low) & (data["Prix au m² (€)"] <= high)]
    data = data.sort_values("Date de vente")
    data.to_excel(target, sheet_name="Ventes", index=False)
    print(f"{len(data)} ventes gardées ({before - len(data)} prix au m² extrêmes retirés), "
          f"du {data['Date de vente'].min():%d/%m/%Y} au {data['Date de vente'].max():%d/%m/%Y}")
    print(data.groupby("Type de bien")["Prix au m² (€)"].describe()[["count", "25%", "50%", "75%"]].round(0))
