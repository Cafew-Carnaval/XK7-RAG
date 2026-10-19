import re
import sqlite3
import sql_agent
from sql_agent import ask, PROMPT_LOG
from sql_schema import connect, DB_PATH
from eval_sql_questions import QUESTIONS
from data_upload import to_sqlite, delete_file


def normalize(value):
    """Rend deux valeurs comparables : '2009' et 2009 sont égaux, 49.6200001 et 49.62 aussi."""
    if isinstance(value, str):
        try:
            value = float(value)
        except ValueError:
            return value.strip().lower()
    if isinstance(value, (int, float)):
        return round(float(value), 2)
    return value


def row_matches(expected_row, agent_row):
    """Chaque valeur attendue doit se retrouver dans la ligne de l'agent.
    Un nombre doit être égal à une cellule ; un texte peut être inclus dans une cellule
    (ex. "Peacock" dans "Jane Peacock")."""
    cells = [normalize(v) for v in agent_row]
    texts = [c for c in cells if isinstance(c, str)]
    for v in map(normalize, expected_row):
        if isinstance(v, float):
            if v not in cells:
                return False
        elif not any(v in t for t in texts):
            return False
    return True


def results_match(expected, agent_rows, ordered):
    """Même nombre de lignes, et chaque ligne attendue retrouvée (dans l'ordre si le classement compte)."""
    if len(expected) != len(agent_rows):
        return False
    if ordered:
        return all(row_matches(e, a) for e, a in zip(expected, agent_rows))
    remaining = list(agent_rows)
    for e in expected:
        match = next((a for a in remaining if row_matches(e, a)), None)
        if match is None:
            return False
        remaining.remove(match)
    return True


def sensitive_values(db_path=DB_PATH, min_length=4):
    """Toutes les valeurs texte de la base (noms, villes, emails, titres...) : aucune ne doit
    apparaître dans ce qui est envoyé au modèle."""
    conn = connect(db_path)
    values = set()
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
    for table in tables:
        for row in conn.execute(f'SELECT * FROM "{table}"'):
            values.update(v.strip() for v in row if isinstance(v, str) and len(v.strip()) >= min_length)
    conn.close()
    return values


def check_confidentiality(questions, db_paths=(DB_PATH,)):
    """Cherche des données de la base dans tout ce qui a été envoyé au modèle.
    On retire d'abord ce qui ne vient pas de la base : le texte fixe du prompt, la question de
    l'utilisateur et les requêtes écrites par le modèle lui-même. Il reste la structure et les
    messages d'erreur : aucune valeur de la base ne doit s'y trouver."""
    fixed = sql_agent.SQL_PROMPT + sql_agent.RETRY_TEXT
    fixed_lines = {line.strip() for line in fixed.split("\n") if line.strip() and "{" not in line}
    sent = "\n".join(PROMPT_LOG)
    sent = re.sub(r"```sql.*?```", " ", sent, flags=re.DOTALL)           # requêtes écrites par le modèle
    for q in questions:
        sent = sent.replace(q, " ")
    sent = "\n".join(l for l in sent.split("\n") if l.strip() not in fixed_lines)
    values = set().union(*(sensitive_values(p) for p in db_paths))
    return sorted(v for v in values
                  if re.search(rf"(?<!\w){re.escape(v)}(?!\w)", sent, re.IGNORECASE))


def prepare_bases():
    """Convertit une fois chaque fichier de test (Excel, CSV) en base SQLite temporaire."""
    bases = {None: DB_PATH}
    for q in QUESTIONS:
        f = q.get("base")
        if f and f not in bases:
            with open(f, "rb") as fh:
                bases[f] = to_sqlite(f, fh.read())
    return bases


def main():
    bases = prepare_bases()
    ok = total = 0
    scores = {}
    print(f"{'Niveau':<12}{'Résultat':<12}{'Essais':<8}Question")
    for q in QUESTIONS:
        db = bases[q.get("base")]
        result = ask(q["question"], db)
        tries = len(result["attempts"]) + (1 if result["ok"] else 0)
        if "refus" in q:
            good = result["refused"] == q["refus"]
            label = "refus OK" if good else f"ÉCHEC ({result['refused'] or 'a répondu'})"
        else:
            conn = connect(db)
            expected = conn.execute(q["reference"]).fetchall()
            conn.close()
            good = result["ok"] and results_match(expected, result["rows"], q["ordre"])
            label = "juste" if good else ("FAUX" if result["ok"] else "ERREUR")
        total += 1
        ok += good
        group = "base inconnue" if q.get("base") else "Chinook"
        scores.setdefault(group, [0, 0])
        scores[group][0] += good
        scores[group][1] += 1
        print(f"{q['niveau']:<12}{label:<12}{tries:<8}{q['question']}")
        if not good and result.get("sql"):
            print(f"{'':<32}requête de l'agent : {' '.join(result['sql'].split())}")
    print(f"\nRésultats justes ou refus corrects : {ok}/{total}")
    for group, (good, n) in scores.items():
        print(f"  {group} : {good}/{n}")
    leaks = check_confidentiality([q["question"] for q in QUESTIONS], list(bases.values()))
    for f, path in bases.items():
        if f:
            delete_file(path)
    print(f"Prompts envoyés au modèle : {len(PROMPT_LOG)}")
    if leaks:
        print(f"CONFIDENTIALITÉ : {len(leaks)} valeurs de la base envoyées au modèle, ex. {leaks[:5]}")
    else:
        print("Confidentialité : aucune donnée de la base envoyée au modèle")


if __name__ == "__main__":
    main()
