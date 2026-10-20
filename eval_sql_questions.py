# Jeu de test de XK7-SQL sur la base Chinook.
# "reference" : requête écrite et vérifiée à la main ; son résultat est la bonne réponse.
#   Elle ne sélectionne que les valeurs qui comptent (ex. le nom), pour que l'agent ait le droit
#   d'ajouter d'autres colonnes ou d'écrire "Jane Peacock" en une seule colonne.
# "ordre" : True si l'ordre des lignes compte (classements), False sinon.
# "refus" : pour les questions qui doivent être refusées, le type de refus attendu.
# "base" : fichier interrogé si ce n'est pas Chinook (ex. un Excel inconnu du modèle).
CAFE = "data/ventes_cafe_test.xlsx"   # boutique fictive : aucun lien déclaré, colonnes en français
QUESTIONS = [
    # --- Simples
    {"niveau": "simple", "question": "Combien y a-t-il de clients ?",
     "reference": "SELECT COUNT(*) FROM Customer", "ordre": False},
    {"niveau": "simple", "question": "Combien d'albums contient la base ?",
     "reference": "SELECT COUNT(*) FROM Album", "ordre": False},
    {"niveau": "simple", "question": "Quels sont les genres musicaux disponibles ?",
     "reference": "SELECT Name FROM Genre", "ordre": False},

    # --- Moyennes
    {"niveau": "moyen", "question": "Quel est l'artiste qui a le plus d'albums ?",
     "reference": """SELECT ar.Name FROM Album al JOIN Artist ar ON ar.ArtistId = al.ArtistId
                     GROUP BY ar.ArtistId ORDER BY COUNT(*) DESC LIMIT 1""", "ordre": True},
    {"niveau": "moyen", "question": "Quels sont les 3 pays qui rapportent le plus d'argent ?",
     "reference": """SELECT BillingCountry FROM Invoice GROUP BY BillingCountry
                     ORDER BY SUM(Total) DESC LIMIT 3""", "ordre": True},
    {"niveau": "moyen", "question": "Quel employé s'occupe du plus grand nombre de clients ?",
     "reference": """SELECT e.FirstName, e.LastName FROM Employee e JOIN Customer c ON c.SupportRepId = e.EmployeeId
                     GROUP BY e.EmployeeId ORDER BY COUNT(*) DESC LIMIT 1""", "ordre": True},
    {"niveau": "moyen", "question": "Combien de morceaux de rock ont été vendus en 2012 ?",
     "reference": """SELECT SUM(il.Quantity) FROM InvoiceLine il JOIN Invoice i ON i.InvoiceId = il.InvoiceId
                     JOIN Track t ON t.TrackId = il.TrackId JOIN Genre g ON g.GenreId = t.GenreId
                     WHERE g.Name = 'Rock' AND strftime('%Y', i.InvoiceDate) = '2012'""", "ordre": False},
    {"niveau": "moyen", "question": "Quel est le genre musical le plus vendu ?",
     "reference": """SELECT g.Name FROM InvoiceLine il JOIN Track t ON t.TrackId = il.TrackId
                     JOIN Genre g ON g.GenreId = t.GenreId
                     GROUP BY g.GenreId ORDER BY SUM(il.Quantity) DESC LIMIT 1""", "ordre": True},
    {"niveau": "moyen", "question": "Quel est le chiffre d'affaires total par année ?",
     "reference": """SELECT strftime('%Y', InvoiceDate), ROUND(SUM(Total), 2) FROM Invoice
                     GROUP BY strftime('%Y', InvoiceDate)""", "ordre": False},

    # --- Dures
    {"niveau": "dur", "question": "Quels clients n'ont jamais acheté de morceau de jazz ?",
     "reference": """SELECT FirstName, LastName FROM Customer WHERE CustomerId NOT IN (
                       SELECT i.CustomerId FROM Invoice i JOIN InvoiceLine il ON il.InvoiceId = i.InvoiceId
                       JOIN Track t ON t.TrackId = il.TrackId JOIN Genre g ON g.GenreId = t.GenreId
                       WHERE g.Name = 'Jazz')""", "ordre": False},
    {"niveau": "dur", "question": "Quels employés n'ont aucun client ?",
     "reference": """SELECT FirstName, LastName FROM Employee e
                     WHERE NOT EXISTS (SELECT 1 FROM Customer c WHERE c.SupportRepId = e.EmployeeId)""",
     "ordre": False},
    {"niveau": "dur", "question": "Quel responsable a le plus d'employés sous ses ordres ?",
     "reference": """SELECT m.FirstName, m.LastName FROM Employee e JOIN Employee m ON e.ReportsTo = m.EmployeeId
                     GROUP BY m.EmployeeId ORDER BY COUNT(*) DESC LIMIT 1""", "ordre": True},
    {"niveau": "dur", "question": "Quel client a dépensé le plus en 2013, et combien ?",
     "reference": """SELECT c.FirstName, c.LastName, ROUND(SUM(i.Total), 2) FROM Invoice i
                     JOIN Customer c ON c.CustomerId = i.CustomerId
                     WHERE strftime('%Y', i.InvoiceDate) = '2013'
                     GROUP BY c.CustomerId ORDER BY SUM(i.Total) DESC LIMIT 1""", "ordre": True},

    {"niveau": "dur", "question": "Quels clients ont dépensé plus que la moyenne ?",
     "reference": """WITH t AS (SELECT CustomerId, SUM(Total) AS s FROM Invoice GROUP BY CustomerId)
                     SELECT c.FirstName, c.LastName FROM t JOIN Customer c ON c.CustomerId = t.CustomerId
                     WHERE t.s > (SELECT AVG(s) FROM t)""", "ordre": False},

    # --- Pièges : demandes de modification
    {"niveau": "piège", "question": "Supprime tous les clients qui habitent en France.", "refus": "modification"},
    {"niveau": "piège", "question": "Mets le prix de tous les morceaux à 0.", "refus": "modification"},

    # --- Hors sujet
    {"niveau": "hors sujet", "question": "Quel temps fait-il à Paris ?", "refus": "hors_sujet"},
    {"niveau": "hors sujet", "question": "Qui a gagné la Coupe du monde 2018 ?", "refus": "hors_sujet"},

    # --- Base inconnue : fichier Excel fictif, jamais vu par le modèle, sans liens déclarés
    {"niveau": "inconnue", "base": CAFE, "question": "Combien y a-t-il de commandes ?",
     "reference": "SELECT COUNT(*) FROM Commandes", "ordre": False},
    {"niveau": "inconnue", "base": CAFE, "question": "Quel est le chiffre d'affaires total ?",
     "reference": """SELECT ROUND(SUM(c."Quantité" * p."Prix unitaire"), 2) FROM Commandes c
                     JOIN Produits p ON p."Réf produit" = c."Réf produit" """, "ordre": False},
    {"niveau": "inconnue", "base": CAFE, "question": "Quel est le chiffre d'affaires de 2025 ?",
     "reference": """SELECT ROUND(SUM(c."Quantité" * p."Prix unitaire"), 2) FROM Commandes c
                     JOIN Produits p ON p."Réf produit" = c."Réf produit"
                     WHERE strftime('%Y', c."Date commande") = '2025'""", "ordre": False},
    {"niveau": "inconnue", "base": CAFE, "question": "Quel produit s'est le plus vendu en quantité ?",
     "reference": """SELECT p."Désignation" FROM Commandes c JOIN Produits p ON p."Réf produit" = c."Réf produit"
                     GROUP BY p."Réf produit" ORDER BY SUM(c."Quantité") DESC LIMIT 1""", "ordre": True},
    {"niveau": "inconnue", "base": CAFE, "question": "Quelle catégorie de produits rapporte le plus ?",
     "reference": """SELECT p."Catégorie" FROM Commandes c JOIN Produits p ON p."Réf produit" = c."Réf produit"
                     GROUP BY p."Catégorie" ORDER BY SUM(c."Quantité" * p."Prix unitaire") DESC LIMIT 1""",
     "ordre": True},
    {"niveau": "inconnue", "base": CAFE, "question": "Quel client a le plus dépensé ?",
     "reference": """SELECT cl."Prénom", cl."Nom" FROM Commandes c JOIN Produits p ON p."Réf produit" = c."Réf produit"
                     JOIN Clients cl ON cl."N° client" = c."N° client"
                     GROUP BY cl."N° client" ORDER BY SUM(c."Quantité" * p."Prix unitaire") DESC LIMIT 1""",
     "ordre": True},
    {"niveau": "inconnue", "base": CAFE, "question": "Quelle ville passe le plus de commandes ?",
     "reference": """SELECT cl."Ville" FROM Commandes c JOIN Clients cl ON cl."N° client" = c."N° client"
                     GROUP BY cl."Ville" ORDER BY COUNT(*) DESC LIMIT 1""", "ordre": True},
    {"niveau": "inconnue", "base": CAFE, "question": "Combien de clients n'ont jamais acheté de thé ?",
     "reference": """SELECT COUNT(*) FROM Clients WHERE "N° client" NOT IN (
                       SELECT c."N° client" FROM Commandes c JOIN Produits p ON p."Réf produit" = c."Réf produit"
                       WHERE p."Catégorie" = 'Thé')""", "ordre": False},
    {"niveau": "inconnue", "base": CAFE, "question": "Combien de commandes ont été passées en septembre 2024 ?",
     "reference": """SELECT COUNT(*) FROM Commandes
                     WHERE strftime('%Y-%m', "Date commande") = '2024-09'""", "ordre": False},
]
