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
]
