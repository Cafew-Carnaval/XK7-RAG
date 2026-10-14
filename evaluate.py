import json
from langchain_openai import ChatOpenAI
from eval_questions import QUESTIONS
from rag import answer, retrieve, REFUS

KS = [3, 6, 10]

# Juge : un petit modèle qui vérifie si chaque point clé attendu figure dans la réponse
judge_llm = ChatOpenAI(model="gpt-4o-mini", temperature=0,
                       model_kwargs={"response_format": {"type": "json_object"}})

JUDGE_PROMPT = """Tu es correcteur. Voici une question, la réponse d'un assistant et une liste de points clés.
Pour chaque point clé, indique s'il est présent dans la réponse : même idée, même si les mots sont différents.
Un point contredit ou seulement effleuré compte comme absent.

Question : {question}

Réponse de l'assistant :
{reply}

Points clés :
{points}

Renvoie uniquement un JSON de la forme {{"points": [true, false, ...]}}, un booléen par point, dans l'ordre."""


def rank_of_first_good(question, source, expected_pages, max_k=10):
    """Renvoie la position du premier bon passage (bon document et bonne page), ou None."""
    hits = retrieve(question, k=max_k)   # même recherche que l'agent (français + anglais)
    for position, h in enumerate(hits, start=1):
        if h["source"] == source and h["page"] in expected_pages:
            return position
    return None


def check_points(question, reply, points):
    """Demande au juge quels points clés sont présents. Renvoie une liste de booléens."""
    numbered = "\n".join(f"{i}. {p}" for i, p in enumerate(points, start=1))
    raw = judge_llm.invoke(JUDGE_PROMPT.format(question=question, reply=reply, points=numbered)).content
    try:
        found = json.loads(raw)["points"]
    except (ValueError, KeyError, TypeError):
        return [False] * len(points)
    found = [bool(x) for x in found][:len(points)]
    return found + [False] * (len(points) - len(found))


def main():
    scores = {k: 0 for k in KS}
    nb = nb_repond = nb_justes = 0
    points_ok = points_total = 0
    missing = []   # points manquants, pour savoir quoi améliorer

    print(f"{'Niveau':<11}{'Document':<17}{'Rang':<6}{'Réponse':<10}{'Points':<8}Question")
    for q in QUESTIONS:
        reply, _ = answer(q["question"], k=3)
        refuse = REFUS in reply

        # Question hors corpus : la bonne réponse est le refus
        if not q["pages"]:
            result = "refus OK" if refuse else "ÉCHEC"
            print(f"{q['niveau']:<11}{'-':<17}{'':<6}{result:<10}{'':<8}{q['question']}")
            continue

        # Questions normales : position du bon passage, l'agent répond-il, la réponse est-elle juste ?
        nb += 1
        rank = rank_of_first_good(q["question"], q["source"], q["pages"])
        for k in KS:
            if rank is not None and rank <= k:
                scores[k] += 1
        if not refuse:
            nb_repond += 1

        found = [False] * len(q["points"]) if refuse else check_points(q["question"], reply, q["points"])
        points_ok += sum(found)
        points_total += len(found)
        if all(found):
            nb_justes += 1
        missing += [(q["question"], p) for p, ok in zip(q["points"], found) if not ok]

        print(f"{q['niveau']:<11}{q['source']:<17}{str(rank) if rank else '-':<6}"
              f"{'refuse' if refuse else 'répond':<10}{f'{sum(found)}/{len(found)}':<8}{q['question']}")

    print()
    for k in KS:
        print(f"Bon passage dans les {k} premiers : {scores[k]}/{nb}")
    print(f"L'agent répond (k=3) : {nb_repond}/{nb}")
    print(f"Points clés présents : {points_ok}/{points_total}")
    print(f"Réponses complètes (tous les points) : {nb_justes}/{nb}")

    if missing:
        print("\nPoints manquants :")
        for question, point in missing:
            print(f"- {question}\n    -> {point}")


if __name__ == "__main__":
    main()
