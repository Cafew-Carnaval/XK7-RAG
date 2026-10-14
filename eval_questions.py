# Jeu de test : question, document et pages attendus (pages vides = l'agent doit refuser), niveau
QUESTIONS = [
    # --- Cours SQL
    {"question": "À quoi sert l'instruction commit ?", "source": "Cours SQL", "pages": [114], "niveau": "simple"},
    {"question": "À quoi sert la clause order by ?", "source": "Cours SQL", "pages": [81], "niveau": "simple"},
    {"question": "Que se passe-t-il quand on fait un drop table ?", "source": "Cours SQL", "pages": [25], "niveau": "simple"},
    {"question": "À quoi sert la clause having ?", "source": "Cours SQL", "pages": [73, 74], "niveau": "moyen"},
    {"question": "Quels sont les types de jointure externe et leur syntaxe ?", "source": "Cours SQL", "pages": [61, 62], "niveau": "moyen"},
    {"question": "Quelle est la différence entre une sous-requête indépendante et une sous-requête corrélée ?", "source": "Cours SQL", "pages": [83, 84], "niveau": "moyen"},
    {"question": "Quelles sont les règles de passage du modèle relationnel au modèle physique ?", "source": "Cours SQL", "pages": [11], "niveau": "dur"},
    {"question": "Qu'est-ce qu'une clé primaire ?", "source": "Cours SQL", "pages": [22], "niveau": "dur"},
    {"question": "Comment réaliser une division en SQL ?", "source": "Cours SQL", "pages": [97, 98, 99], "niveau": "dur"},
    # --- Database Design (en anglais : la question est en français)
    {"question": "Quelles sont les règles de la deuxième forme normale ?", "source": "Database Design", "pages": [72, 73, 74, 75, 78], "niveau": "moyen"},
    {"question": "Qu'est-ce qu'une dépendance fonctionnelle ?", "source": "Database Design", "pages": [67, 68, 69, 70, 71], "niveau": "moyen"},
    # --- Red Book (en anglais)
    {"question": "Pourquoi l'isolation faible est-elle si répandue alors qu'elle peut provoquer des anomalies ?", "source": "Red Book", "pages": [19, 20, 21, 22], "niveau": "expert"},
    # --- Hors corpus
    {"question": "Comment créer un index vectoriel avec pgvector dans PostgreSQL ?", "source": None, "pages": [], "niveau": "impossible"},
]
