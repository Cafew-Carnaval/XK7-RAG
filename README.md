# XK7-RAG

Un assistant IA qui répond aux questions sur les bases de données et le SQL à partir d'une bibliothèque d'ouvrages, en citant ses sources, et qui dit quand il ne sait pas.

XK7-RAG s'appuie sur quatre documents (un cours de SQL, un manuel de conception de bases de données, la documentation officielle de PostgreSQL 18 et un recueil de recherche, plus de 1 300 pages au total). Il retrouve les passages utiles, puis GPT-4o rédige une réponse en français uniquement à partir de ces passages, avec le document et la page de chaque information. Si la réponse n'est pas dans les documents, il le dit au lieu d'inventer.

C'est le premier agent d'un futur assistant data multi-agent : documentation, requêtes SQL, analyse et qualité des données.

## Démo en ligne

L'application est déployée sur Streamlit Community Cloud : https://xk7-rag.streamlit.app

L'accès est protégé par un mot de passe (chaque question appelle l'API OpenAI, donc a un coût). Il est disponible sur demande.

## Pourquoi ce projet

- Monter en compétence sur les technologies d'IA générative les plus demandées : RAG, embeddings, bases vectorielles, LLM, évaluation, et bientôt les systèmes multi-agents.
- Répondre à un besoin réel : retrouver vite une information fiable dans une documentation longue, sans relire des centaines de pages, et pouvoir vérifier la source.
- Construire une brique réutilisable pour la suite du projet.

## Pourquoi pas simplement ChatGPT ou Claude ?

Un assistant généraliste répond avec ce qu'il a appris pendant son entraînement. Un RAG répond avec vos documents. Concrètement :

- Des réponses vérifiables : chaque information est accompagnée du document et de la page, on peut aller vérifier.
- Moins d'inventions : l'assistant n'a le droit de répondre qu'à partir des passages retrouvés, et refuse quand l'information n'y est pas.
- Les connaissances propres à l'entreprise : procédures internes, documentation d'un logiciel maison, référentiels de données. Un modèle généraliste ne les connaît pas.
- Des documents à jour : il suffit de réindexer un document pour que l'assistant en tienne compte, sans réentraîner de modèle.
- Le contrôle des données : on choisit exactement quels documents sont consultés, ce qui facilite la gestion des droits et de la confidentialité. Le modèle peut ensuite être remplacé par un modèle hébergé en interne si les documents sont sensibles.
- Un coût maîtrisé : on n'envoie au modèle que quelques passages, pas des documents entiers.
- Une brique réutilisable : le même moteur peut servir dans une application web, une API ou un système multi-agent.

## Ce qu'il fait

- Répond en français, même quand la source est en anglais.
- Cite ses sources sous la forme (Titre du document, p. X) et n'affiche sous la réponse que les passages réellement cités.
- Refuse de répondre quand l'information n'est pas dans les documents, et donne une réponse partielle en précisant ce qui manque quand c'est le cas.
- Adapte sa façon d'expliquer à trois niveaux : Débutant (grand public, avec des images de la vie courante), Intermédiaire (bac+1 à bac+5) et Expert (bac+6 et plus, définitions formelles et cas limites). Le contenu reste le même, seule la manière d'expliquer change.
- Garde le fil de la conversation : une question de suivi comme "et pour rollback ?" est réécrite pour être comprise seule.
- Ne recopie que du code SQL présent dans les documents, sans en inventer.

## Corpus

| Document | Auteurs | Licence | Contenu |
|---|---|---|---|
| Cours SQL | Cours universitaire public | Cours public | Modèle relationnel, SQL de base à avancé (142 pages) |
| Database Design, 2nd Edition | Adrienne Watt, Nelson Eng | CC BY 4.0 | Conception, modélisation, normalisation |
| PostgreSQL 18 Documentation | The PostgreSQL Global Development Group | Licence PostgreSQL | Parties Tutorial, The SQL Language et SQL Commands uniquement |
| Readings in Database Systems, 5th Edition (Red Book) | Peter Bailis, Joseph M. Hellerstein, Michael Stonebraker | CC BY-NC-ND | Recherche : transactions, isolation, architecture des SGBD |

Les documents ne sont pas versionnés dans le dépôt. Le Red Book est sous licence non commerciale : le projet est un projet personnel, gratuit et non commercial.

## Fonctionnement

```mermaid
flowchart LR
    A[PDF] --> B[Lecture et nettoyage]
    B --> C[Découpage par sections]
    C --> D[Embeddings]
    D --> E[(ChromaDB)]
    Q[Question] --> R[Réécriture avec l'historique]
    R --> T[Recherche en français et en anglais]
    E --> T
    T --> S[Ajout du reste de la section]
    S --> G[GPT-4o rédige la réponse au niveau choisi]
    G --> F[Réponse avec sources citées]
```

| Étape | Fichier | Détail |
|---|---|---|
| Lecture et nettoyage | `loader.py` | Extraction du texte avec pypdf, correction des défauts des PDF (ligatures, petites majuscules, mots coupés, en-têtes et numéros de page), retrait des pages de sommaire |
| Découpage | `loader.py` | Découpage selon les sections du document grâce aux signets du PDF, sous-sections numérotées comprises. Chaque morceau commence par le chemin de sa section (ex. PostgreSQL 18 > 11. Indexes > 11.8. Partial Indexes) |
| Indexation | `vectorstore.py` | Embeddings OpenAI text-embedding-3-small, stockés dans ChromaDB (3 803 morceaux) |
| Recherche | `rag.py` | La question est cherchée en français et traduite en anglais, les résultats des deux recherches sont alternés |
| Contexte | `rag.py` | Chaque passage trouvé est complété par le reste de sa section, comme un lecteur qui lit toute la section après avoir trouvé le bon paragraphe |
| Réponse | `rag.py` | GPT-4o (température 0) répond uniquement à partir des passages, au niveau choisi, en citant ses sources |
| Interface | `app.py` | Application Streamlit : conversation, choix du niveau, nombre de passages, sources citées, mot de passe et limite de questions |
| Évaluation | `evaluate.py`, `eval_questions.py` | 18 questions de test, mesure de la recherche et de la justesse des réponses |
| Thème | `.streamlit/config.toml` | Thème sombre de l'interface |

## Technologies

Python 3.12, LangChain, OpenAI (GPT-4o, GPT-4o-mini, text-embedding-3-small), ChromaDB, Streamlit, pypdf.

## Installation

Prérequis : Python 3.12 et une clé API OpenAI.

```bash
git clone https://github.com/Cafew-Carnaval/XK7-RAG.git
cd XK7-RAG
py -3.12 -m venv .venv
.venv\Scripts\activate          # Windows
pip install -r requirements.txt
```

Créer un fichier `.env` à la racine (il n'est jamais versionné) :

```
OPENAI_API_KEY=sk-...
APP_PASSWORD=un-mot-de-passe-dedie-au-projet
```

Placer les PDF dans le dossier `data/` avec les noms attendus par `loader.py` (`test.pdf`, `database_design.pdf`, `postgresql.pdf`, `redbook.pdf`), puis :

```bash
python vectorstore.py      # construit la base vectorielle
streamlit run app.py       # lance l'interface sur http://localhost:8501
python evaluate.py         # lance l'évaluation
```

## Choix techniques

- Python 3.12 plutôt que la dernière version : ChromaDB et numpy n'avaient pas encore de versions compatibles avec Python 3.15.
- Nettoyage du texte avant indexation : les PDF générés avec LaTeX produisaient des caractères corrompus qui empêchaient de retrouver certains mots. Chaque correction a été vérifiée en comparant le texte extrait au PDF d'origine. Les réglages (en-tête, numéro de page, pages à lire) sont définis document par document dans un catalogue.
- Découpage par sections plutôt que par pages : le découpage par pages séparait parfois un titre de son contenu. Avec les signets du PDF, chaque morceau sait à quelle section il appartient.
- Recherche bilingue : trois documents sur quatre sont en anglais. Une question en français retrouvait mal les passages anglais (le bon passage du Red Book passait du 6e au 1er rang avec la traduction).
- Garde-fous contre les inventions : réponse uniquement à partir des extraits, pas de code SQL inventé, comparaisons pédagogiques autorisées mais jamais en contradiction avec les extraits. Ces règles ont été ajoutées après avoir observé de vraies erreurs pendant les tests.
- Base vectorielle versionnée : la base ChromaDB est poussée sur GitHub pour que l'application en ligne n'ait pas à recalculer les embeddings au démarrage.
- Sécurité de la démo : mot de passe dédié, nombre de questions limité par session, clés dans les secrets de Streamlit et jamais dans le code.

## Évaluation

Le jeu de test compte 18 questions : 9 sur le cours SQL (3 simples, 3 moyennes, 3 difficiles), 2 sur Database Design, 2 de niveau expert sur le Red Book, 4 sur PostgreSQL 18 et 1 question hors corpus à laquelle l'agent doit refuser de répondre.

Le script `evaluate.py` mesure trois choses :

- La recherche : à quel rang arrive le premier bon passage (bon document et bonne page).
- La réponse : l'agent répond-il, et refuse-t-il bien la question hors corpus ?
- La justesse : chaque question a 2 ou 3 points clés attendus (par exemple, pour la division en SQL : la double négation avec not exists, la méthode par comptage, l'exemple des membres inscrits à toutes les activités). GPT-4o-mini joue le rôle de correcteur et vérifie la présence de chaque point dans la réponse.

Évolution des résultats au fil du projet :

| Étape | Questions | L'agent répond | Refus hors corpus |
|---|---|---|---|
| Découpage par pages | 9 | 6/9 | oui |
| Découpage par sections | 9 | 8/9 | oui |
| Prompt pédagogique et sections complètes | 9 | 9/9 | oui |
| Corpus de 3 documents, recherche bilingue | 12 | 12/12 | oui |
| Ajout de PostgreSQL 18 (3 803 morceaux) | 17 | 17/17 | oui |

Sur la dernière version, le bon passage arrive dans les 3 premiers résultats pour 15 questions sur 17.

La justesse mesurée par les points clés sera ajoutée au tableau au prochain lancement de l'évaluation.

## Limites actuelles

- Le correcteur automatique est lui-même un modèle de langage : il peut se tromper, et les points clés ne couvrent pas toute la qualité d'une réponse (clarté, adaptation au niveau). Une relecture humaine reste utile.
- Le jeu de test est petit (18 questions). Les résultats donnent une tendance, pas une mesure fiable à la décimale près.
- La documentation de référence de PostgreSQL est très dense : pour la question sur string_agg, la page attendue n'arrive pas dans les 10 premiers passages, même si l'agent répond.
- Temps de réponse d'environ 20 secondes : réécriture, traduction, deux recherches et une génération avec GPT-4o s'enchaînent.
- La base vectorielle fait environ 65 Mo. Ajouter beaucoup d'autres documents demandera de la construire au déploiement ou de passer par Git LFS.

## Prochaines étapes

- Un agent SQL qui écrit et exécute des requêtes sur une vraie base, puis un système multi-agent avec LangGraph.
- Réduire le temps de réponse (appels en parallèle, cache, modèle plus léger pour certaines étapes).
- Recherche hybride (sens et mots-clés) et reranking pour les documentations très denses.
- API avec FastAPI, conteneur Docker et suivi des appels avec LangSmith.

## Sources

- Cours universitaire public de bases de données et SQL.
- Watt, A. et Eng, N. Database Design, 2nd Edition. BCcampus, licence CC BY 4.0.
- The PostgreSQL Global Development Group. PostgreSQL 18 Documentation, licence PostgreSQL.
- Bailis, P., Hellerstein, J. M. et Stonebraker, M. Readings in Database Systems, 5th Edition, 2015, licence CC BY-NC-ND.

## Auteur

[Cafew-Carnaval sur GitHub](https://github.com/Cafew-Carnaval)
