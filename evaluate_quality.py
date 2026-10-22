import os
import re
import quality_agent
from quality_agent import check_quality, summary, RULES_LOG, INFO
from data_upload import to_sqlite, delete_file
from make_quality_test import CLEAN_FILE, DIRTY_FILE, ERREURS
import make_quality_test


def load(path):
    with open(path, "rb") as f:
        return to_sqlite(path, f.read())


def found(report, error, dirty_tables):
    """L'erreur injectée est-elle détectée ? Il faut un problème du bon type, dans la bonne table,
    qui touche la bonne ligne. Pour les règles métier, on cherche l'identifiant de la ligne dans les exemples."""
    table = report["tables"].get(error["table"])
    if not table:
        return False
    for p in table["problemes"]:
        if p["gravite"] == INFO:
            continue
        if error["type"] == "règle métier" and p["type"] == "règle métier":
            ident = dirty_tables[error["table"]].iloc[error["ligne"], 0]   # 1re colonne = identifiant
            if any(ident in map(str, e.values()) for e in p["exemples"]):
                return True
        elif p["type"] == error["type"] and error["ligne"] in p["lignes"]:
            return True
    return False


def main(use_rules=True):
    if not (os.path.exists(CLEAN_FILE) and os.path.exists(DIRTY_FILE)):
        import subprocess, sys
        subprocess.run([sys.executable, "make_quality_test.py"], check=True)

    dirty = {"Clients": make_quality_test._dirty_clients, "Commandes": make_quality_test._dirty_commandes}
    db_clean, db_dirty = load(CLEAN_FILE), load(DIRTY_FILE)
    try:
        # 1. Fichier sale : combien d'erreurs injectées sont retrouvées ?
        rep = check_quality(db_path=db_dirty, use_rules=use_rules)
        print("=== Fichier avec erreurs injectées\n" + summary(rep) + "\n")
        by_type = {}
        for e in ERREURS:
            ok = found(rep, e, dirty)
            by_type.setdefault(e["type"], [0, 0])
            by_type[e["type"]][0] += ok
            by_type[e["type"]][1] += 1
            if not ok:
                print(f"  MANQUÉE : {e['table']} · {e['type']} · ligne {e['ligne']}")
        total_ok = sum(v[0] for v in by_type.values())
        print(f"\nErreurs injectées retrouvées : {total_ok}/{len(ERREURS)}")
        for k, (g, n) in by_type.items():
            print(f"  {k} : {g}/{n}")

        # 2. Fichier propre : l'agent ne doit rien signaler (sinon ce sont des fausses alertes)
        rep_clean = check_quality(db_path=db_clean, use_rules=use_rules)
        false_alarms = [(t, p) for t, r in rep_clean["tables"].items() for p in r["problemes"] if p["gravite"] != INFO]
        print(f"\n=== Fichier propre\n{summary(rep_clean)}")
        print(f"Fausses alertes : {len(false_alarms)}")
        for t, p in false_alarms:
            print(f"  {t} · {p['type']} · {p['detail']}")

        # 3. Confidentialité : aucune valeur des fichiers dans ce qui a été envoyé au modèle
        if use_rules:
            sent = re.sub(r"Demande : .*", "", "\n".join(RULES_LOG))
            values = set()
            for df in list(dirty.values()) + [make_quality_test._dirty_produits]:
                for col in df.columns:
                    values.update(str(v).strip() for v in df[col].dropna() if isinstance(v, str) and len(str(v).strip()) >= 4)
            leaks = sorted(v for v in values if re.search(rf"(?<!\w){re.escape(v)}(?!\w)", sent, re.IGNORECASE))
            print(f"\nPrompts envoyés au modèle : {len(RULES_LOG)}")
            print("Confidentialité : " + (f"{len(leaks)} valeurs envoyées, ex. {leaks[:5]}" if leaks
                                         else "aucune donnée envoyée au modèle"))
            print("\nRègles proposées (fichier sale) :")
            for r in rep["regles"]:
                print(f"  - {r['regle']} : {r['statut']}" + (f" ({r['nb']} lignes)" if r.get("nb") else ""))
                if r.get("erreur"):
                    print(f"      erreur : {r['erreur']}\n      requête : {' '.join(r['sql'].split())}")
    finally:
        delete_file(db_clean)
        delete_file(db_dirty)


if __name__ == "__main__":
    main()
