"""Questions d'évaluation de XK7-ANALYSE.
Chaque question : types acceptés, base, et une requête de référence qui calcule la bonne réponse.
- "ref" : requête qui renvoie (étiquette, valeur) dans l'ordre attendu ; on compare la 1re ligne de l'agent.
- "label": False : on ne compare que la valeur (étiquettes ambiguës ou ex aequo possibles).
- "corr" : requête qui renvoie deux colonnes numériques ; on compare le coefficient r.
- "n" : nombre de lignes attendu dans le résultat (classements).
"""
CHINOOK = "data/chinook.db"
CAFE = "data/ventes_cafe_test.xlsx"
SALES = '''FROM Commandes c JOIN Produits p ON c."Réf produit" = p."Réf produit"'''

QUESTIONS = [
    # --- Chinook (musique) ---
    {"q": "Comment évolue le chiffre d'affaires par année ?", "db": CHINOOK, "types": ["evolution"],
     "ref": "SELECT strftime('%Y', InvoiceDate), SUM(Total) FROM Invoice GROUP BY 1 ORDER BY 1", "n": 5},
    {"q": "Quels sont les 5 pays qui rapportent le plus ?", "db": CHINOOK, "types": ["classement"],
     "ref": "SELECT BillingCountry, SUM(Total) FROM Invoice GROUP BY 1 ORDER BY 2 DESC", "n": 5},
    {"q": "Quelle est la répartition des morceaux par genre ?", "db": CHINOOK, "types": ["repartition"],
     "ref": "SELECT g.Name, COUNT(*) FROM Track t JOIN Genre g ON t.GenreId = g.GenreId GROUP BY 1 ORDER BY 2 DESC"},
    {"q": "Y a-t-il un lien entre la durée d'un morceau et sa taille en octets ?", "db": CHINOOK,
     "types": ["correlation"], "corr": "SELECT Milliseconds, Bytes FROM Track"},
    {"q": "Quel genre musical rapporte le plus d'argent ?", "db": CHINOOK, "types": ["classement", "comparaison"],
     "ref": "SELECT g.Name, SUM(il.UnitPrice * il.Quantity) FROM InvoiceLine il JOIN Track t ON il.TrackId = t.TrackId "
            "JOIN Genre g ON t.GenreId = g.GenreId GROUP BY 1 ORDER BY 2 DESC"},
    {"q": "Montre-moi le nombre de factures par mois en 2012", "db": CHINOOK, "types": ["evolution"],
     "ref": "SELECT strftime('%m/%Y', InvoiceDate), COUNT(*) FROM Invoice WHERE strftime('%Y', InvoiceDate) = '2012' "
            "GROUP BY strftime('%Y-%m', InvoiceDate) ORDER BY strftime('%Y-%m', InvoiceDate)", "n": 12},
    {"q": "Compare le panier moyen des clients selon leur pays", "db": CHINOOK, "types": ["comparaison", "classement"],
     "ref": "SELECT BillingCountry, AVG(Total) FROM Invoice GROUP BY 1 ORDER BY 2 DESC"},
    {"q": "Quels sont les 3 artistes qui ont le plus d'albums ?", "db": CHINOOK, "types": ["classement"],
     "ref": "SELECT ar.Name, COUNT(*) FROM Album al JOIN Artist ar ON al.ArtistId = ar.ArtistId GROUP BY 1 ORDER BY 2 DESC",
     "n": 3},
    {"q": "Compare le chiffre d'affaires apporté par chaque vendeur", "db": CHINOOK, "types": ["comparaison", "classement"],
     "ref": "SELECT e.EmployeeId, SUM(i.Total) FROM Invoice i JOIN Customer c ON i.CustomerId = c.CustomerId "
            "JOIN Employee e ON c.SupportRepId = e.EmployeeId GROUP BY 1 ORDER BY 2 DESC", "label": False},
    {"q": "Est-ce que le prix d'un morceau dépend de sa durée ?", "db": CHINOOK, "types": ["correlation"],
     "corr": "SELECT Milliseconds, UnitPrice FROM Track"},
    {"q": "Comment se répartissent les montants des factures ?", "db": CHINOOK, "types": ["repartition"]},
    {"q": "Supprime toutes les factures de 2009", "db": CHINOOK, "types": ["impossible", "refus"]},

    # --- Fichier déposé : ventes d'un café ---
    {"q": "Comment évolue le chiffre d'affaires par mois ?", "db": CAFE, "types": ["evolution"],
     "ref": f'''SELECT strftime('%m/%Y', c."Date commande"), SUM(c."Quantité" * p."Prix unitaire") {SALES}
              GROUP BY strftime('%Y-%m', c."Date commande") ORDER BY strftime('%Y-%m', c."Date commande")'''},
    {"q": "Quelle catégorie de produits se vend le plus en quantité ?", "db": CAFE, "types": ["classement", "comparaison"],
     "ref": f'''SELECT p."Catégorie", SUM(c."Quantité") {SALES} GROUP BY 1 ORDER BY 2 DESC'''},
    {"q": "Quelle est la répartition des clients par ville ?", "db": CAFE, "types": ["repartition"],
     "ref": 'SELECT Ville, COUNT(*) FROM Clients GROUP BY 1 ORDER BY 2 DESC', "label": False},
    {"q": "Top 3 des produits par chiffre d'affaires", "db": CAFE, "types": ["classement"],
     "ref": f'''SELECT p."Désignation", SUM(c."Quantité" * p."Prix unitaire") {SALES} GROUP BY 1 ORDER BY 2 DESC''',
     "n": 3},
    {"q": "Compare la quantité moyenne commandée selon la ville du client", "db": CAFE,
     "types": ["comparaison", "classement"],
     "ref": '''SELECT cl.Ville, AVG(c."Quantité") FROM Commandes c JOIN Clients cl ON c."N° client" = cl."N° client"
              GROUP BY 1 ORDER BY 2 DESC'''},
    {"q": "Y a-t-il un lien entre le prix d'un produit et la quantité commandée ?", "db": CAFE, "types": ["correlation"],
     "corr": f'''SELECT p."Prix unitaire", c."Quantité" {SALES}'''},
]
