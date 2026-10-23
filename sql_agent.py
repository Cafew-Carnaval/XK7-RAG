import os
import re
import time
import sqlite3
from dotenv import load_dotenv
from sql_schema import get_schema, connect, DB_PATH

load_dotenv()


def get_llm():
    """Choisit le modèle selon .env : LLM_PROVIDER=openai (par défaut) ou ollama (100 % local, plus tard)."""
    provider = os.getenv("LLM_PROVIDER", "openai")
    if provider == "ollama":
        from langchain_ollama import ChatOllama   # pip install langchain-ollama (seulement pour le mode local)
        return ChatOllama(model=os.getenv("OLLAMA_MODEL", "qwen2.5-coder:7b"), temperature=0)
    from langchain_openai import ChatOpenAI
    return ChatOpenAI(model="gpt-4o", temperature=0)


llm = get_llm()

OFF_TOPIC = "HORS_SUJET"
WRITE_REQUEST = "MODIFICATION"
PROMPT_LOG = []       # tout ce qui est envoyé au modèle, pour vérifier la confidentialité
MAX_ROWS = 100         # lignes affichées au maximum
MAX_SECONDS = 5        # durée maximale d'une requête
MAX_ATTEMPTS = 3       # 1 essai + 2 corrections

SQL_PROMPT = """Tu es un expert SQL. Tu écris une requête SQLite qui répond à la question, à partir de la structure de la base.
Tu ne vois que la structure (tables, colonnes, liens), jamais les données.

Règles :
- Une seule requête, uniquement un SELECT (ou WITH ... SELECT). Jamais INSERT, UPDATE, DELETE, DROP, ALTER, CREATE.
- Utilise exactement les noms de tables et de colonnes de la structure. Si un nom contient des espaces,
  des accents ou des caractères spéciaux, mets-le entre guillemets doubles (ex. "Prix unitaire").
- Fais les jointures grâce aux liens indiqués par "->".
- Tu ne connais pas les valeurs exactes des données. Elles sont probablement dans la même langue que
  les noms des colonnes (colonnes en anglais : valeurs en anglais, ex. 'Germany' ; colonnes en français :
  valeurs en français, ex. 'Allemagne'). Pour filtrer sur un texte, utilise LIKE, qui ignore les majuscules.
- Les dates sont des textes au format 'AAAA-MM-JJ HH:MM:SS' : utilise strftime('%Y', colonne) pour l'année.
  Un mois sans année ("en août") désigne ce mois pour TOUTES les années : strftime('%m', colonne) = '08'.
  N'invente jamais une année que la question ne donne pas.
- Pour compter des articles vendus, additionne les quantités (SUM) s'il existe une colonne de quantité.
- Pour "jamais", "aucun", "n'a pas", "sans" : exclus tous ceux qui ont AU MOINS UNE fois la propriété,
  avec NOT EXISTS ou NOT IN sur une sous-requête. Un simple <> ou une jointure externe filtrée ne suffit pas :
  quelqu'un qui a acheté un produit A ET un produit B a quand même acheté A.
- Compare toujours des valeurs de même niveau : pour comparer le total d'un client (ou d'un produit,
  d'un pays...) à "la moyenne", calcule la moyenne de ces mêmes totaux (d'abord un total par client,
  puis la moyenne de ces totaux), et non la moyenne des lignes de détail.
- Pour un type ou une famille (ex. "les produits bio", "les films d'action"), filtre de préférence sur une colonne de
  catégorie ou de genre si elle existe, plutôt que sur un mot dans un nom de produit.
- Pour "quel client", "quel employé", "quel produit"... renvoie aussi son nom (prénom et nom, désignation),
  avec une jointure si le nom est dans une autre table, pas seulement son identifiant.
- Pour "le plus" ou "le moins", trie et garde la première ligne (ORDER BY ... DESC LIMIT 1). N'utilise pas
  MAX() ou MIN() avec d'autres colonnes non agrégées dans le même SELECT.
- Donne des noms de colonnes lisibles avec AS.
- Sauf si la question demande tout, limite le résultat à 100 lignes (LIMIT 100).
- Si la question demande de modifier, ajouter ou supprimer des données (ou le code pour le faire), tu n'exécutes rien :
  tu PROPOSES la requête, que l'utilisateur relira et lancera lui-même. Réponds alors exactement dans ce format :
{write_request}
```sql
la requête de modification (UPDATE, DELETE ou INSERT), une seule
```
```sql
un SELECT qui renvoie les lignes que cette modification toucherait (mêmes conditions), avec leurs colonnes utiles
```
Explication : une phrase en français qui dit ce que fait la modification.
- Si la question ne peut pas être traitée avec cette base (sujet sans rapport), réponds uniquement : {off_topic}

Réponds exactement dans ce format :
```sql
la requête
```
Explication : une phrase en français qui dit ce que fait la requête.

Structure de la base :
{schema}

Question : {question}
{previous_error}"""

PROPOSAL_NOTE = ("Requête proposée, **non exécutée** : XK7-SQL est en lecture seule. Relis-la, puis lance-la "
                 "toi-même sur une copie de la base. Les lignes qu'elle toucherait sont affichées ci-dessous.")

REFUSALS = {
    "hors_sujet": "Je ne peux pas répondre : cette question ne porte pas sur le contenu de la base de données.",
    "modification": "Je ne peux pas faire ça : XK7-SQL est en lecture seule, il ne modifie, n'ajoute "
                    "et ne supprime jamais de données.",
}

RETRY_TEXT = """
Ta requête précédente a échoué :
```sql
{sql}
```
Erreur : {error}
Corrige la requête en respectant les mêmes règles et le même format."""


# --- Étape D : sécurité -------------------------------------------------------------

FORBIDDEN = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|ATTACH|DETACH|PRAGMA|VACUUM|REINDEX|REPLACE\s+INTO)\b",
    re.IGNORECASE,
)


class UnsafeQuery(Exception):
    """Requête refusée avant même d'être exécutée."""


def check_sql(sql):
    """Première barrière : refuse tout ce qui n'est pas une seule requête de lecture.
    (La deuxième barrière est la base ouverte en lecture seule.)"""
    # On retire les commentaires et les textes entre apostrophes avant de chercher les mots interdits :
    # WHERE Name LIKE '%Drop%' est une requête normale, pas une attaque
    code = re.sub(r"--[^\n]*|/\*.*?\*/", " ", sql, flags=re.DOTALL)
    code = re.sub(r"'(?:[^']|'')*'", "''", code).strip().rstrip(";").strip()

    if ";" in code:
        raise UnsafeQuery("Une seule requête est autorisée.")
    if not re.match(r"(SELECT|WITH)\b", code, re.IGNORECASE):
        raise UnsafeQuery("Seules les requêtes de lecture (SELECT) sont autorisées.")
    found = FORBIDDEN.search(code)
    if found:
        raise UnsafeQuery(f"Mot interdit dans la requête : {found.group(1).upper()}.")


def run_sql(sql, db_path=DB_PATH, max_rows=MAX_ROWS, max_seconds=MAX_SECONDS):
    """Exécute la requête en lecture seule, avec une limite de temps et de lignes.
    Renvoie (noms des colonnes, lignes, tronqué ?). Le résultat n'est jamais renvoyé au modèle."""
    check_sql(sql)
    conn = connect(db_path)
    deadline = time.monotonic() + max_seconds
    # SQLite appelle cette fonction régulièrement pendant l'exécution : renvoyer 1 interrompt la requête
    conn.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 10_000)
    try:
        cur = conn.execute(sql)
        columns = [d[0] for d in cur.description] if cur.description else []
        rows = cur.fetchmany(max_rows + 1)      # une ligne de plus pour savoir s'il y en avait davantage
        return columns, rows[:max_rows], len(rows) > max_rows
    except sqlite3.OperationalError as e:
        if "interrupted" in str(e):
            raise sqlite3.OperationalError(f"Requête trop longue (plus de {max_seconds} secondes).")
        raise
    finally:
        conn.close()


# --- Vérification des valeurs : le modèle devine les valeurs sans voir les données -------

def literal_values(sql):
    """Valeurs texte utilisées comme filtres dans la requête : après =, <>, !=, LIKE, ou dans IN (...)."""
    found = re.findall(r"(?:=|<>|!=|\bLIKE)\s*'((?:[^']|'')*)'", sql, re.IGNORECASE)
    for group in re.findall(r"\bIN\s*\(([^()]*'[^()]*)\)", sql, re.IGNORECASE):
        found += re.findall(r"'((?:[^']|'')*)'", group)
    # On ignore les nombres, les dates et les formats de strftime ('%Y', '2012', '2024-01')
    return {v.replace("''", "'") for v in found if re.search(r"[^\W\d_]", v.replace("%", "").strip("YmdHMSjwWf-: "))}


def missing_values(sql, db_path=DB_PATH):
    """Renvoie les valeurs filtrées par la requête qui n'existent nulle part dans la base.
    La vérification se fait localement : aucune donnée n'est envoyée au modèle, on lui dira seulement
    qu'une valeur qu'il a lui-même devinée n'existe pas."""
    values = literal_values(sql)
    if not values:
        return []
    conn = connect(db_path)
    try:
        columns = []
        for (table,) in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"):
            columns += [(table, c[1]) for c in conn.execute(f'PRAGMA table_info("{table}")')]
        missing = []
        for value in sorted(values):
            # LIKE sans % = égalité qui ignore les majuscules ; avec % = motif
            exists = any(conn.execute(f'SELECT 1 FROM "{t}" WHERE "{c}" LIKE ? LIMIT 1', (value,)).fetchone()
                         for t, c in columns)
            if not exists:
                missing.append(value)
        return missing
    finally:
        conn.close()


# --- Étapes C et E : écrire la requête, puis se corriger si elle échoue ----------------

def generate_sql(question, schema, previous_sql=None, error=None):
    """Demande au modèle d'écrire la requête (ou de corriger la précédente).
    Renvoie (requête, explication), ou (None, "hors_sujet" / "modification") si la question est refusée."""
    previous_error = RETRY_TEXT.format(sql=previous_sql, error=error) if error else ""
    prompt = SQL_PROMPT.format(schema=schema, question=question, off_topic=OFF_TOPIC,
                               write_request=WRITE_REQUEST, previous_error=previous_error)
    PROMPT_LOG.append(prompt)
    reply = llm.invoke(prompt).content.strip()

    if reply.startswith(WRITE_REQUEST):
        blocks = re.findall(r"```(?:sql)?\s*(.+?)```", reply, re.DOTALL | re.IGNORECASE)
        explanation = re.search(r"Explication\s*:\s*(.+)", reply)
        if len(blocks) >= 2:
            return {"write": blocks[0].strip(), "preview": blocks[1].strip(),
                    "explanation": explanation.group(1).strip() if explanation else ""}, "modification"
        return None, "modification"
    if reply.startswith(OFF_TOPIC):
        return None, "hors_sujet"

    match = re.search(r"```(?:sql)?\s*(.+?)```", reply, re.DOTALL | re.IGNORECASE)
    sql = match.group(1).strip() if match else reply
    explanation = re.search(r"Explication\s*:\s*(.+)", reply)
    return sql, explanation.group(1).strip() if explanation else ""


WRITE_START = re.compile(r"^\s*(UPDATE|DELETE|INSERT|REPLACE)\b", re.IGNORECASE)


def check_write_sql(sql, db_path=DB_PATH):
    """Vérifie qu'une requête de modification PROPOSÉE est valide, sans jamais l'exécuter :
    EXPLAIN demande à SQLite de la compiler (tables et colonnes vérifiées) sans la lancer,
    et la base est de toute façon ouverte en lecture seule."""
    if not WRITE_START.match(sql) or ";" in sql.strip().rstrip(";"):
        raise sqlite3.OperationalError("La proposition doit être une seule requête UPDATE, DELETE ou INSERT.")
    conn = connect(db_path)
    try:
        conn.execute("EXPLAIN " + sql.strip().rstrip(";"))
    finally:
        conn.close()


def top_level_keyword(sql, keyword, start=0):
    """Position du premier mot-clé (ex. WHERE) qui n'est ni entre parenthèses (sous-requête) ni entre guillemets
    ou apostrophes, ou -1."""
    depth, quote, i = 0, None, start
    while i < len(sql):
        ch = sql[i]
        if quote:
            if ch == quote:
                quote = None
        elif ch in "'\"":
            quote = ch
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif depth == 0 and re.match(rf"{keyword}\b", sql[i:], re.IGNORECASE) and (i == 0 or not sql[i - 1].isalnum()):
            return i
        i += 1
    return -1


def derived_preview(write_sql):
    """Aperçu calculé par le code à partir de la modification proposée : mêmes table et mêmes conditions,
    donc exactement les lignes touchées. None pour les cas complexes (INSERT, UPDATE ... FROM)."""
    sql = write_sql.strip().rstrip(";").strip()
    table = r'("(?:[^"]|"")+"|\[[^\]]+\]|[\w.]+)'
    m = re.match(rf"DELETE\s+FROM\s+{table}\s*", sql, re.IGNORECASE)
    if not m:
        m = re.match(rf"UPDATE\s+(?:OR\s+\w+\s+)?{table}\s+SET\s", sql, re.IGNORECASE)
        if not m or top_level_keyword(sql, "FROM", m.end()) != -1:     # UPDATE ... FROM : trop complexe
            return None
    where = top_level_keyword(sql, "WHERE", m.end())
    condition = sql[where:] if where != -1 else ""
    if re.search(r"\b(RETURNING|ORDER\s+BY|LIMIT)\b", condition, re.IGNORECASE):
        return None
    return f"SELECT * FROM {m.group(1)} {condition}".strip()


def propose(question, schema, proposal, db_path, attempts):
    """Demande de modification : on vérifie la requête proposée et on montre les lignes concernées.
    Rien n'est modifié. En cas d'erreur, le modèle corrige sa proposition (mêmes règles que pour un SELECT)."""
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            check_write_sql(proposal["write"], db_path)
            proposal["preview"] = derived_preview(proposal["write"]) or proposal["preview"]
            columns, rows, truncated = run_sql(proposal["preview"], db_path)
            if not rows and attempt == 1:
                # Aperçu vide : souvent une valeur ou une année inventée. Une seule vérification, puis on accepte
                raise sqlite3.OperationalError(
                    "L'aperçu ne renvoie aucune ligne. Vérifie les filtres : n'as-tu pas inventé une valeur ou une "
                    "année absente de la question ? Si la question est bien respectée, renvoie la même proposition.")
            return {"ok": False, "refused": "modification", "message": PROPOSAL_NOTE, "attempts": attempts,
                    "write_sql": proposal["write"], "sql": proposal["preview"],
                    "explanation": proposal["explanation"], "columns": columns, "rows": rows, "truncated": truncated}
        except (sqlite3.Error, UnsafeQuery) as e:
            error = str(e)
            attempts.append({"sql": proposal["write"], "error": error})
            if attempt == MAX_ATTEMPTS:
                break
            retry, kind = generate_sql(question, schema, f"{proposal['write']}\n\n{proposal['preview']}", error)
            if not isinstance(retry, dict):
                break
            proposal = retry
    return {"ok": False, "refused": "modification", "message": REFUSALS["modification"], "attempts": attempts}


def ask(question, db_path=DB_PATH):
    """Point d'entrée de XK7-SQL : question en français -> requête -> résultat.
    Seuls la structure et les messages d'erreur SQL sont envoyés au modèle, jamais les données."""
    schema = get_schema(db_path)
    sql, error, attempts = None, None, []
    for attempt in range(1, MAX_ATTEMPTS + 1):
        sql, explanation = generate_sql(question, schema, sql, error)
        if isinstance(sql, dict):              # demande de modification : proposition, jamais exécutée
            return propose(question, schema, sql, db_path, attempts)
        if sql is None:
            return {"ok": False, "refused": explanation, "message": REFUSALS[explanation], "attempts": attempts}
        try:
            columns, rows, truncated = run_sql(sql, db_path)
            missing = missing_values(sql, db_path)
            warning = None
            if missing:
                listed = ", ".join(f"'{v}'" for v in missing)
                if attempt < MAX_ATTEMPTS:
                    # La requête a tourné, mais filtre sur une valeur inventée : résultat sans doute faux
                    error = (f"La requête filtre sur {listed}, qui n'existe dans aucune colonne de la base. "
                             "La valeur est sans doute dans une autre langue ou une autre orthographe : "
                             "essaie une autre valeur plausible, ou un LIKE plus large.")
                    attempts.append({"sql": sql, "error": error})
                    continue
                warning = (f"Attention : la requête filtre sur {listed}, qui n'existe pas dans la base. "
                           "Le résultat est probablement faux.")
            return {"ok": True, "refused": None, "sql": sql, "explanation": explanation, "columns": columns,
                    "rows": rows, "truncated": truncated, "attempts": attempts, "warning": warning}
        except UnsafeQuery as e:
            # Pas de nouvel essai : une requête dangereuse est refusée définitivement
            attempts.append({"sql": sql, "error": f"Refusée par la sécurité : {e}"})
            return {"ok": False, "refused": "securite", "sql": sql, "attempts": attempts,
                    "message": f"Requête refusée par la sécurité : {e}"}
        except sqlite3.Error as e:
            error = str(e)
            attempts.append({"sql": sql, "error": error})
    return {"ok": False, "refused": None, "sql": sql,
            "message": f"Échec après {MAX_ATTEMPTS} essais. Dernière erreur : {error}", "attempts": attempts}


def show(result):
    """Affichage simple dans le terminal."""
    for a in result["attempts"]:
        print(f"  essai raté : {a['error']}")
    if not result["ok"]:
        print(result["message"])
        return
    print(result["sql"])
    print(f"Explication : {result['explanation']}")
    if result.get("warning"):
        print(result["warning"])
    print(result["columns"])
    for row in result["rows"][:10]:
        print("  ", row)
    more = " (tronqué)" if result["truncated"] else ""
    print(f"({len(result['rows'])} lignes{more})")


if __name__ == "__main__":
    # 1. Les barrières de sécurité, testées directement (sans passer par le modèle)
    print("=== Tests de sécurité")
    for sql in ["DELETE FROM Customer",
                "SELECT * FROM Customer; DROP TABLE Customer",
                "WITH x AS (SELECT 1) DELETE FROM Customer",
                "SELECT Name FROM Artist WHERE Name LIKE '%Drop%'"]:
        try:
            check_sql(sql)
            print(f"accepté : {sql}")
        except UnsafeQuery as e:
            print(f"refusé  : {sql}  ->  {e}")

    # 2. Des questions, dont une piège
    for question in [
        "Combien de morceaux de rock ont été vendus en 2012 ?",
        "Quel employé s'occupe du plus grand nombre de clients ?",
        "Supprime tous les clients qui habitent en France.",
        "Quel temps fait-il à Paris ?",
    ]:
        print(f"\n=== {question}")
        show(ask(question))
