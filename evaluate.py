from eval_questions import QUESTIONS
from rag import answer, retrieve, REFUS

KS = [3, 6, 10]


def rank_of_first_good(question, source, expected_pages, max_k=10):
    """Renvoie la position du premier bon passage (bon document et bonne page), ou None."""
    hits = retrieve(question, k=max_k)   # même recherche que l'agent (français + anglais)
    for position, h in enumerate(hits, start=1):
        if h["source"] == source and h["page"] in expected_pages:
            return position
    return None


def main():
    scores = {k: 0 for k in KS}
    nb = 0
    nb_repond = 0

    print(f"{'Niveau':<11}{'Document':<17}{'Rang':<6}{'Réponse':<10}Question")
    for q in QUESTIONS:
        reply, _ = answer(q["question"], k=3)
        refuse = REFUS in reply

        # Question hors corpus : l'agent doit refuser
        if not q["pages"]:
            result = "refus OK" if refuse else "ÉCHEC"
            print(f"{q['niveau']:<11}{'-':<17}{'':<6}{result:<10}{q['question']}")
            continue

        # Questions normales : position du bon passage + l'agent répond-il ?
        nb += 1
        rank = rank_of_first_good(q["question"], q["source"], q["pages"])
        for k in KS:
            if rank is not None and rank <= k:
                scores[k] += 1
        if not refuse:
            nb_repond += 1
        print(f"{q['niveau']:<11}{q['source']:<17}{str(rank) if rank else '-':<6}"
              f"{'refuse' if refuse else 'répond':<10}{q['question']}")

    print()
    for k in KS:
        print(f"Bon passage dans les {k} premiers : {scores[k]}/{nb}")
    print(f"L'agent répond (k=3) : {nb_repond}/{nb}")


if __name__ == "__main__":
    main()
