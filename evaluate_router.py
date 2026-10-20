import time
from router import route, AGENTS
from eval_router_questions import QUESTIONS


def main():
    good = 0
    by_type = {}
    confusion = {a: {b: 0 for b in AGENTS} for a in AGENTS}   # attendu -> choisi
    start = time.time()

    print(f"{'Type':<12}{'Attendu':<12}{'Choisi':<12}Question")
    for q in QUESTIONS:
        history = [{"role": "user", "content": h} for h in q.get("history", [])]
        decision = route(q["question"], history)
        ok = decision["agent"] == q["agent"]
        good += ok
        by_type.setdefault(q["type"], [0, 0])
        by_type[q["type"]][0] += ok
        by_type[q["type"]][1] += 1
        confusion[q["agent"]][decision["agent"]] += 1
        mark = "" if ok else "   <- ERREUR : " + decision["raison"]
        print(f"{q['type']:<12}{q['agent']:<12}{decision['agent']:<12}{q['question']}{mark}")

    print(f"\nBon agent choisi : {good}/{len(QUESTIONS)}")
    for t, (g, n) in by_type.items():
        print(f"  {t} : {g}/{n}")
    print(f"Temps moyen par décision : {(time.time() - start) / len(QUESTIONS):.1f} s")

    print("\nMatrice de confusion (ligne = attendu, colonne = choisi) :")
    print(f"{'':<12}" + "".join(f"{a:<12}" for a in AGENTS))
    for a in AGENTS:
        print(f"{a:<12}" + "".join(f"{confusion[a][b]:<12}" for b in AGENTS))


if __name__ == "__main__":
    main()
