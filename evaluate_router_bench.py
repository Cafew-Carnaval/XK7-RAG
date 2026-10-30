"""Banc de test du routeur XK7-CORE (~500 questions).

    python evaluate_router_bench.py                 -> lot de RÉGLAGE (60 %) : erreurs détaillées, on corrige le prompt
    python evaluate_router_bench.py --controle      -> lot de CONTRÔLE (40 % + les questions de l'utilisateur) :
                                                       le vrai score, à lancer à la fin, sans corriger après coup
    python evaluate_router_bench.py --tout

Règle : on ne lit jamais les erreurs du lot de contrôle pour corriger le prompt, sinon le score n'est plus honnête.
"""
import hashlib
import json
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from router import route, AGENTS
from router_bench import BENCH, MINE
from sql_schema import DB_PATH
from data_upload import to_sqlite

FILES = {"CAFE": "ventes_cafe_test.xlsx", "IMMO": "ventes_immobilier_44.xlsx", "SALE": "qualite_sale.xlsx"}


# Questions déjà vues en erreur pendant le réglage : elles restent au réglage quoi qu'il arrive
SEEN = {"Quel pourcentage des ventes vient d'Europe ?"}


def lot_of(question, history):
    """Lot d'une question, fixé par l'empreinte de son texte : il ne dépend pas des autres questions, donc ajouter,
    retirer ou corriger une question ne fait jamais changer de lot les autres (60 % réglage, 40 % contrôle)."""
    key = (question + "|" + "|".join(history)).encode("utf-8")
    if question in SEEN:
        return "reglage"
    return "reglage" if int(hashlib.sha256(key).hexdigest(), 16) % 100 < 60 else "controle"


def items():
    """Toutes les questions, avec leur lot. Les questions écrites par l'utilisateur vont toutes au contrôle."""
    result = []
    for row, mine in [(r, False) for r in BENCH] + [(r, True) for r in MINE]:
        agent, base, question = row[:3]
        history = row[3] if len(row) > 3 else []
        result.append({"agent": agent, "base": base, "piege": question.startswith("!"),
                       "question": question.lstrip("!"), "history": history,
                       "lot": "controle" if mine else lot_of(question.lstrip("!"), history), "mine": mine})
    return result


def databases():
    """Une vraie base par fichier, comme dans l'application : le routeur voit les vrais noms de tables."""
    dbs = {"C": (DB_PATH, "la base de démonstration Chinook")}
    for key, name in FILES.items():
        path = Path("data") / name
        dbs[key] = (to_sqlite(name, path.read_bytes()), f"le fichier déposé par l'utilisateur ({name})")
    return dbs


def ask(q, dbs):
    db_path, source = dbs[q["base"]]
    # Comme dans l'application, l'historique contient les questions ET l'agent qui a répondu : la question
    # précédente est d'abord routée elle-même, et c'est son agent qui est mis dans l'historique
    history = []
    for h in q["history"]:
        before = route(h, history, db_path=db_path, source=source)["agent"]
        history += [{"role": "user", "content": h}, {"role": "assistant", "agent": before, "content": ""}]
    start = time.time()
    for attempt in range(3):   # une erreur réseau ponctuelle ne doit pas compter comme une erreur de routage
        try:
            d = route(q["question"], history, db_path=db_path, source=source)
            break
        except Exception as exc:  # noqa: BLE001
            if attempt == 2:
                d = {"agent": "erreur", "raison": str(exc)[:80]}
            time.sleep(2)
    return {**q, "choisi": d["agent"], "raison": d.get("raison", ""), "duree": time.time() - start}


def score(rows, label):
    good = sum(r["choisi"] == r["agent"] for r in rows)
    return f"{label:<22}{good:>4}/{len(rows):<4} {100 * good / max(len(rows), 1):5.1f} %"


def main():
    lot = "controle" if "--controle" in sys.argv else "tout" if "--tout" in sys.argv else "reglage"
    questions = [q for q in items() if lot == "tout" or q["lot"] == lot]
    dbs = databases()
    print(f"Lot : {lot} · {len(questions)} questions · envoi en parallèle…\n")
    start = time.time()
    with ThreadPoolExecutor(max_workers=8) as pool:
        rows = list(pool.map(lambda q: ask(q, dbs), questions))
    total = time.time() - start

    errors = [r for r in rows if r["choisi"] != r["agent"]]
    if lot != "controle" or "--details" in sys.argv:
        print("ERREURS")
        for r in sorted(errors, key=lambda r: (r["agent"], r["choisi"])):
            hist = f" [après : {r['history'][-1]}]" if r["history"] else ""
            print(f"  {r['agent']:<11}-> {r['choisi']:<11}{'(piège) ' if r['piege'] else ''}"
                  f"[{r['base']}] {r['question']}{hist}\n{'':<26}{r['raison']}")
    else:
        print("(lot de contrôle : erreurs masquées, ajoute --details pour les voir une fois le travail fini)")

    print("\nRÉSULTATS")
    print(score(rows, "Total"))
    print(score([r for r in rows if not r["piege"]], "Questions normales"))
    print(score([r for r in rows if r["piege"]], "Pièges"))
    print(score([r for r in rows if r["history"]], "Questions de suivi"))
    if any(r["mine"] for r in rows):
        print(score([r for r in rows if r["mine"]], "Écrites à la main"))
    print("\nPar agent attendu (rappel)")
    for a in AGENTS:
        print("  " + score([r for r in rows if r["agent"] == a], a))
    print("\nPar base")
    for b in ["C", *FILES]:
        print("  " + score([r for r in rows if r["base"] == b], b))

    print("\nMatrice de confusion (ligne = attendu, colonne = choisi)")
    cols = list(AGENTS) + (["erreur"] if any(r["choisi"] == "erreur" for r in rows) else [])
    confusion = Counter((r["agent"], r["choisi"]) for r in rows)
    print(f"{'':<12}" + "".join(f"{c[:9]:>10}" for c in cols))
    for a in AGENTS:
        print(f"{a:<12}" + "".join(f"{confusion[(a, c)] or '·':>10}" for c in cols))
    print(f"\nDurée : {total:.0f} s au total, {sum(r['duree'] for r in rows) / len(rows):.1f} s par décision")

    Path(f"router_bench_{lot}.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
