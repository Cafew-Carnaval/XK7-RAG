# Jeu de test du routeur XK7-CORE : question -> agent attendu (sur la base Chinook).
# "piège" : questions où un mot trompeur pourrait envoyer au mauvais agent.
QUESTIONS = [
    # --- Cours et documentation -> rag
    {"type": "rag", "question": "C'est quoi une clé étrangère ?", "agent": "rag"},
    {"type": "rag", "question": "Explique la deuxième forme normale.", "agent": "rag"},
    {"type": "rag", "question": "À quoi sert un index partiel dans PostgreSQL ?", "agent": "rag"},
    {"type": "rag", "question": "Quelle est la différence entre INNER JOIN et LEFT JOIN ?", "agent": "rag"},
    {"type": "rag", "question": "Pourquoi l'isolation faible est-elle si répandue ?", "agent": "rag"},
    {"type": "rag", "question": "Comment écrire une requête récursive avec WITH ?", "agent": "rag"},
    {"type": "rag", "question": "Que veut dire ACID pour une transaction ?", "agent": "rag"},

    # --- Questions sur les données -> sql
    {"type": "sql", "question": "Combien de clients habitent au Canada ?", "agent": "sql"},
    {"type": "sql", "question": "Quels sont les 5 artistes qui ont le plus d'albums ?", "agent": "sql"},
    {"type": "sql", "question": "Quel est le chiffre d'affaires de 2012 ?", "agent": "sql"},
    {"type": "sql", "question": "Liste les employés avec le nom de leur responsable.", "agent": "sql"},
    {"type": "sql", "question": "Quel genre musical se vend le mieux en France ?", "agent": "sql"},
    {"type": "sql", "question": "Donne la durée moyenne des morceaux par genre.", "agent": "sql"},
    {"type": "sql", "question": "Montre les factures de plus de 20 euros.", "agent": "sql"},

    # --- Pièges
    {"type": "piège", "question": "Combien de types de jointures existe-t-il en SQL ?", "agent": "rag"},
    {"type": "piège", "question": "Explique-moi comment fonctionne GROUP BY avec un exemple.", "agent": "rag"},
    {"type": "piège", "question": "Quels clients n'ont jamais acheté de jazz ?", "agent": "sql"},
    {"type": "piège", "question": "Supprime toutes les factures de 2009.", "agent": "sql"},
    {"type": "piège", "question": "Quels sont nos meilleurs clients ?", "agent": "sql"},
    {"type": "piège", "question": "Comment mettre à jour plusieurs lignes en une seule requête UPDATE ?", "agent": "rag"},

    # --- Explication + données -> les_deux
    {"type": "les_deux", "question": "Explique le GROUP BY, puis donne le chiffre d'affaires par pays.",
     "agent": "les_deux"},
    {"type": "les_deux", "question": "C'est quoi une jointure externe ? Montre-moi les employés qui n'ont aucun client.",
     "agent": "les_deux"},
    {"type": "les_deux", "question": "Explique ce qu'est une division relationnelle et trouve les clients qui ont acheté dans tous les genres.",
     "agent": "les_deux"},

    # --- Hors sujet
    {"type": "hors_sujet", "question": "Quel temps fait-il à Paris ?", "agent": "hors_sujet"},
    {"type": "hors_sujet", "question": "Donne-moi une recette de crêpes.", "agent": "hors_sujet"},
    {"type": "hors_sujet", "question": "Qui a gagné la Coupe du monde 2018 ?", "agent": "hors_sujet"},

    # --- Questions de suivi : l'historique compte
    {"type": "suivi", "history": ["Quel est le chiffre d'affaires de 2012 ?"],
     "question": "Et en 2013 ?", "agent": "sql"},
    {"type": "suivi", "history": ["C'est quoi la première forme normale ?"],
     "question": "Et la troisième ?", "agent": "rag"},

    # --- Nouvelles questions, ajoutées APRÈS les corrections du prompt : elles vérifient que les
    #     règles marchent en général, et pas seulement sur les questions qui les ont fait naître
    {"type": "nouveau", "question": "Qu'est-ce qu'un trigger et quand l'utiliser ?", "agent": "rag"},
    {"type": "nouveau", "question": "Pourquoi PostgreSQL utilise-t-il le MVCC ?", "agent": "rag"},
    {"type": "nouveau", "question": "Quels pays ont plus de 5 clients ?", "agent": "sql"},
    {"type": "nouveau", "question": "Quels artistes n'ont aucun album vendu ?", "agent": "sql"},
    {"type": "nouveau", "question": "Pourquoi un NOT IN peut-il donner un résultat vide ? Vérifie-le sur les clients sans facture.",
     "agent": "les_deux"},
    {"type": "nouveau", "question": "Quel est le meilleur film de l'année ?", "agent": "hors_sujet"},

    # --- Contrôle de la qualité -> qualite (ajoutées avec XK7-QUALITE)
    {"type": "qualite", "question": "Vérifie la qualité des données.", "agent": "qualite"},
    {"type": "qualite", "question": "Y a-t-il des doublons dans les clients ?", "agent": "qualite"},
    {"type": "qualite", "question": "Les données des factures sont-elles fiables ?", "agent": "qualite"},
    {"type": "qualite", "question": "Trouve les anomalies dans la base.", "agent": "qualite"},
    {"type": "qualite", "question": "Est-ce qu'il manque des valeurs dans la table des employés ?", "agent": "qualite"},
    {"type": "qualite", "question": "Combien de clients n'ont pas de numéro de fax ?", "agent": "sql"},
    {"type": "qualite", "question": "C'est quoi un doublon dans une base de données ?", "agent": "rag"},
    {"type": "qualite", "history": ["Vérifie la qualité des données."],
     "question": "Comment corriger ça ?", "agent": "qualite"},
    {"type": "qualite", "question": "Donne-moi un script SQL pour nettoyer les doublons.", "agent": "qualite"},

    # --- Demandes vagues sur un fichier déposé -> qualite (aperçu complet)
    {"type": "fichier", "source": "le fichier déposé par l'utilisateur (rh_test_erreurs.xlsx)",
     "question": "Regarde l'Excel.", "agent": "qualite"},
    {"type": "fichier", "source": "le fichier déposé par l'utilisateur (rh_test_erreurs.xlsx)",
     "question": "Regarde le document.", "agent": "qualite"},
    {"type": "fichier", "source": "le fichier déposé par l'utilisateur (ventes.csv)",
     "question": "Qu'est-ce que tu penses de mon fichier ?", "agent": "qualite"},
    {"type": "fichier", "source": "le fichier déposé par l'utilisateur (ventes.csv)",
     "question": "Combien de lignes contient mon fichier ?", "agent": "sql"},

    # --- Analyses avec graphique -> analyse ; et les pièges qui restent à sql
    {"type": "analyse", "question": "Comment évolue le chiffre d'affaires par année ?", "agent": "analyse"},
    {"type": "analyse", "question": "Quelle est la répartition des morceaux par genre ?", "agent": "analyse"},
    {"type": "analyse", "question": "Y a-t-il un lien entre la durée d'un morceau et son prix ?", "agent": "analyse"},
    {"type": "analyse", "question": "Compare les ventes entre les pays.", "agent": "analyse"},
    {"type": "analyse", "question": "Fais-moi un graphique du nombre de factures par mois.", "agent": "analyse"},
    {"type": "analyse", "question": "Quelle part du chiffre d'affaires fait chaque genre ?", "agent": "analyse"},
    {"type": "analyse", "source": "le fichier déposé par l'utilisateur (ventes_cafe_test.xlsx)",
     "question": "Analyse les ventes par mois.", "agent": "analyse"},
    {"type": "analyse", "question": "Quel est le chiffre d'affaires par pays ?", "agent": "sql"},
    {"type": "analyse", "question": "C'est quoi une corrélation ?", "agent": "rag"},
    {"type": "analyse", "history": ["Comment évolue le chiffre d'affaires par année ?"],
     "question": "Et par mois ?", "agent": "analyse"},
    # Écrites APRÈS la correction du 23/10, pour vérifier que la règle est générale (pas de surapprentissage)
    {"type": "nouveau", "question": "Quel est le prix moyen des morceaux par type de média ?", "agent": "sql"},
    {"type": "nouveau", "question": "Quel employé a le plus de clients ?", "agent": "sql"},
    {"type": "nouveau", "question": "Comment ont évolué les ventes de rock depuis 2009 ?", "agent": "analyse"},
    {"type": "nouveau", "question": "À quoi servent les verrous ?", "agent": "rag"},
]
