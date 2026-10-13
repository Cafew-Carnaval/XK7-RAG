from eval_questions import QUESTIONS
from vectorstore import search
from rag import answer

KS = [3, 6, 10]
REFUS = "Je ne trouve pas la réponse dans le cours"


def rank_of_first_good(question, expected_pages, max_k=10):
    """Renvoie la position de la première bonne page (1 = en tête), ou None."""
    hits = search(question, k=max_k)
    for position, h in enumerate(hits, start=1):
        if h["page"] in expected_pages:
            return position
    return None


def main():
    scores = {k: 0 for k in KS}
    nb = 0
    nb_repond = 0

    print(f"{'Niveau':<11}{'Rang':<6}{'Réponse':<10}Question")
    for q in QUESTIONS:
        reply, _ = answer(q["question"], k=3)
        refuse = REFUS in reply

        # Question impossible : l'agent doit refuser
        if not q["pages"]:
            result = "refus OK" if refuse else "ÉCHEC"
            print(f"{q['niveau']:<11}{'':<6}{result:<10}{q['question']}")
            continue

        # Questions normales : position de la bonne page + l'agent répond-il ?
        nb += 1
        rank = rank_of_first_good(q["question"], q["pages"])
        for k in KS:
            if rank is not None and rank <= k:
                scores[k] += 1
        if not refuse:
            nb_repond += 1
        print(f"{q['niveau']:<11}{str(rank) if rank else '-':<6}{'refuse' if refuse else 'répond':<10}{q['question']}")

    print()
    for k in KS:
        print(f"Bonne page dans les {k} premiers passages : {scores[k]}/{nb}")
    print(f"L'agent répond (k=3) : {nb_repond}/{nb}")

if __name__ == "__main__":
    main()