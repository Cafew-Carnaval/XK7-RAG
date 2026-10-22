"""Fabrique deux fichiers de test pour XK7-QUALITE (données entièrement fictives) :
- data/qualite_propre.xlsx : une boutique sans erreur, pour vérifier que l'agent ne crie pas au loup ;
- data/qualite_sale.xlsx : la même boutique avec des erreurs injectées volontairement, listées dans ERREURS.
"""
import random
import datetime as dt
import pandas as pd

PRENOMS = ["Camille", "Lucas", "Léa", "Hugo", "Manon", "Nathan", "Chloé", "Théo", "Inès", "Louis", "Sarah", "Jules",
           "Emma", "Adam", "Zoé", "Noah", "Lina", "Gabriel", "Jade", "Raphaël", "Élodie", "Bastien", "Anaïs", "Mathis"]
NOMS = ["Martin", "Bernard", "Petit", "Durand", "Leroy", "Moreau", "Simon", "Laurent", "Lefèvre", "Michel", "Garcia",
        "Roux", "Fournier", "Girard", "Bonnet", "Dupuis", "Lambert", "Fontaine", "Rousseau", "Vincent", "Mercier",
        "Blanc", "Guérin", "Faure"]
VILLES = {"Paris": "75011", "Lyon": "69003", "Marseille": "13006", "Lille": "59000", "Nantes": "44000",
          "Bordeaux": "33000", "Toulouse": "31000", "Rennes": "35000"}
PRODUITS = [("P01", "Carnet ligné A5", "Papeterie", 6.5), ("P02", "Stylo plume", "Écriture", 24.0),
            ("P03", "Cartouches d'encre x10", "Écriture", 4.9), ("P04", "Agenda 2025", "Papeterie", 18.0),
            ("P05", "Crayons de couleur x24", "Dessin", 12.5), ("P06", "Carnet de croquis", "Dessin", 9.9),
            ("P07", "Surligneurs x4", "Écriture", 5.5), ("P08", "Trousse en toile", "Accessoire", 14.0),
            ("P09", "Règle 30 cm", "Accessoire", 2.5), ("P10", "Aquarelle 12 couleurs", "Dessin", 21.0)]


def clean_data(seed=11):
    """Boutique fictive de papeterie, sans aucune erreur."""
    random.seed(seed)
    noms = random.sample([(p, n) for p in PRENOMS for n in NOMS], 40)   # pas d'homonymes
    clients = []
    for i, (prenom, nom) in enumerate(noms, start=1):
        ville = random.choice(list(VILLES))
        clients.append({"N° client": f"C{i:03d}", "Prénom": prenom, "Nom": nom,
                        "Email": f"{prenom.lower()}.{nom.lower()}{i}@exemple.fr".replace("é", "e").replace("è", "e")
                        .replace("ë", "e").replace("ï", "i"),
                        "Ville": ville, "Code postal": VILLES[ville],
                        "Date d'inscription": dt.date(2023, 1, 1) + dt.timedelta(days=random.randint(0, 300))})
    produits = [{"Réf produit": r, "Désignation": d, "Catégorie": c, "Prix unitaire": p} for r, d, c, p in PRODUITS]
    commandes = []
    for i in range(1, 251):
        c = random.choice(clients)
        start = c["Date d'inscription"]
        commandes.append({"N° commande": f"CMD{i:04d}", "N° client": c["N° client"],
                          "Réf produit": random.choice(PRODUITS)[0],
                          "Date commande": start + dt.timedelta(days=random.randint(1, 600)),
                          "Quantité": random.choice([1, 1, 1, 2, 2, 3, 4])})
    return pd.DataFrame(clients), pd.DataFrame(produits), pd.DataFrame(commandes)


def dirty_data(clients, produits, commandes):
    """Copie avec des erreurs injectées. Renvoie les tables et la liste des erreurs attendues :
    (table, type de problème, position de la ligne)."""
    clients, produits, commandes = clients.copy(), produits.copy(), commandes.copy()
    errors = []

    def add(table, kind, row):
        errors.append({"table": table, "type": kind, "ligne": row})

    # Valeurs manquantes
    for row in (3, 17, 28):
        clients.loc[row, "Email"] = None
        add("Clients", "manquant", row)
    # Formats : emails invalides, codes postaux faux, ville mal écrite, espaces en trop
    clients.loc[5, "Email"] = "jean.dupont@"
    add("Clients", "format", 5)
    clients.loc[9, "Email"] = "marie.durand.exemple.fr"
    add("Clients", "format", 9)
    clients.loc[12, "Code postal"] = "750O1"      # lettre O à la place d'un zéro
    add("Clients", "format", 12)
    clients.loc[21, "Code postal"] = "6900"        # un chiffre manquant
    add("Clients", "format", 21)
    clients.loc[14, "Ville"] = clients.loc[14, "Ville"].upper()
    add("Clients", "format", 14)
    clients.loc[30, "Ville"] = clients.loc[30, "Ville"] + " "
    add("Clients", "format", 30)
    # Doublons approchés : deux clients ressaisis avec une autre écriture (majuscules, accents, espaces)
    for src, n in ((7, 41), (19, 42)):
        copy = clients.loc[src].copy()
        copy["N° client"] = f"C{n:03d}"
        copy["Nom"] = copy["Nom"].upper() + " "
        copy["Prénom"] = copy["Prénom"].replace("é", "e").replace("É", "E")
        copy["Email"] = copy["Email"].upper() if copy["Email"] else copy["Email"]
        clients.loc[len(clients)] = copy
        add("Clients", "doublon approché", len(clients) - 1)
    # Doublons exacts : deux commandes saisies deux fois (seul le numéro change)
    for src in (40, 120):
        copy = commandes.loc[src].copy()
        copy["N° commande"] = f"CMD{9000 + src}"
        commandes.loc[len(commandes)] = copy
        add("Commandes", "doublon", len(commandes) - 1)
    # Valeurs aberrantes : quantités négatives et quantité énorme
    for row in (55, 160):
        commandes.loc[row, "Quantité"] = -2
        add("Commandes", "aberrant", row)
    commandes.loc[200, "Quantité"] = 500
    add("Commandes", "aberrant", 200)
    # Liens cassés : commandes d'un client ou d'un produit qui n'existe pas
    for row in (70, 210):
        commandes.loc[row, "N° client"] = "C999"
        add("Commandes", "lien cassé", row)
    commandes.loc[90, "Réf produit"] = "P99"
    add("Commandes", "lien cassé", 90)
    # Règle métier : commandes passées avant l'inscription du client
    dates = dict(zip(clients["N° client"], clients["Date d'inscription"]))
    for row in (30, 130):
        commandes.loc[row, "Date commande"] = dates[commandes.loc[row, "N° client"]] - dt.timedelta(days=20)
        add("Commandes", "règle métier", row)
    return clients, produits, commandes, errors


def save(path, clients, produits, commandes):
    with pd.ExcelWriter(path) as w:
        clients.to_excel(w, sheet_name="Clients", index=False)
        produits.to_excel(w, sheet_name="Produits", index=False)
        commandes.to_excel(w, sheet_name="Commandes", index=False)


CLEAN_FILE, DIRTY_FILE = "data/qualite_propre.xlsx", "data/qualite_sale.xlsx"
_clean = clean_data()
_dirty_clients, _dirty_produits, _dirty_commandes, ERREURS = dirty_data(*_clean)

if __name__ == "__main__":
    save(CLEAN_FILE, *_clean)
    save(DIRTY_FILE, _dirty_clients, _dirty_produits, _dirty_commandes)
    print(f"{CLEAN_FILE} et {DIRTY_FILE} créés : {len(ERREURS)} erreurs injectées")
