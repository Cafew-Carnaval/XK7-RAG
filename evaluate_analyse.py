"""Évalue XK7-ANALYSE : bon type d'analyse, bon résultat (comparé à une requête de référence), confidentialité.
Usage : python evaluate_analyse.py
"""
import re
import time
import sqlite3
import pandas as pd
import analyse_agent
from analyse_agent import analyse
from data_upload import to_sqlite
from eval_analyse_questions import QUESTIONS, CAFE


def same_value(a, b):
    a, b = float(a), float(b)
    return abs(a - b) <= max(0.01, 0.01 * abs(b))


def same_label(a, b):
    return str(a).strip().lower() == str(b).strip().lower()


def check(question, result, db_path):
    """Renvoie (ok, détail)."""
    if result["type"] not in question["types"]:
        return False, f"type {result['type']} au lieu de {'/'.join(question['types'])}"
    if result["type"] in ("impossible", "refus", "echec"):
        return True, result.get("erreur", "")
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        if "corr" in question:
            ref = pd.read_sql_query(question["corr"], conn).apply(pd.to_numeric, errors="coerce").dropna()
            r_ref, r = ref.iloc[:, 0].corr(ref.iloc[:, 1]), result["chart"]["r"]
            return abs(r - r_ref) < 0.02, f"r = {r:.2f} (référence {r_ref:.2f})"
        if "ref" not in question:
            return True, "type seul vérifié"
        ref = conn.execute(question["ref"]).fetchall()
    finally:
        conn.close()
    raw = result["raw"]
    label, value = raw.iloc[0, 0], raw.iloc[0, 1]
    ok = same_value(value, ref[0][1]) and (question.get("label") is False or same_label(label, ref[0][0]))
    detail = f"1re ligne {label} = {value:.2f} (référence {ref[0][0]} = {ref[0][1]:.2f})"
    if "n" in question and len(raw) != question["n"]:
        ok, detail = False, detail + f", {len(raw)} lignes au lieu de {question['n']}"
    return ok, detail


def secret_values(db_path):
    """Valeurs qui ne doivent JAMAIS apparaître dans ce qui est envoyé au modèle : noms, emails, montants."""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    values = set()
    for table, in conn.execute("SELECT name FROM sqlite_master WHERE type='table'"):
        for col in [c[1] for c in conn.execute(f'PRAGMA table_info("{table}")')]:
            if re.search(r"name|nom|email|total|prix|price", col, re.I) and not re.search(r"id$", col, re.I):
                for (v,) in conn.execute(f'SELECT DISTINCT "{col}" FROM "{table}" LIMIT 200'):
                    if v is not None and len(str(v)) >= 5:
                        values.add(str(v))
    conn.close()
    return values


def main():
    cafe_db = to_sqlite("ventes_cafe_test.xlsx", open(CAFE, "rb").read())
    scores, leaks, start = {}, [], time.time()
    for i, question in enumerate(QUESTIONS, 1):
        db_path = cafe_db if question["db"] == CAFE else question["db"]
        analyse_agent.PLAN_LOG.clear()
        result = analyse(question["q"], db_path)
        ok, detail = check(question, result, db_path)
        base = "café" if question["db"] == CAFE else "Chinook"
        scores.setdefault(base, []).append(ok)
        print(f"{'OK ' if ok else 'ERR'} {i:2}. [{result['type']}, {result['essais']} essai(s)] {question['q']}")
        print(f"       {detail}")
        if not ok and result["type"] == "echec":
            print(f"       dernière erreur : {result['erreur']}")
            print(f"       dernier plan : {result['plan']}")
            if analyse_agent.LOCAL_ERRORS:
                print(f"       détail local : {analyse_agent.LOCAL_ERRORS[-1]}")
        sent = "\n".join(analyse_agent.PLAN_LOG)
        found = [v for v in secret_values(db_path) if re.search(rf"(?<!\w){re.escape(v)}(?!\w)", sent)]
        found = [v for v in found if v not in question["q"]]
        if found:
            leaks.append((question["q"], found[:5]))

    print("\n=== Résultats ===")
    for base, oks in scores.items():
        print(f"{base:8} : {sum(oks)}/{len(oks)}")
    total = [ok for oks in scores.values() for ok in oks]
    print(f"Total    : {sum(total)}/{len(total)}  ({time.time() - start:.0f} s)")
    print("Confidentialité :", "aucune donnée envoyée au modèle" if not leaks else f"FUITE {leaks}")


if __name__ == "__main__":
    main()
