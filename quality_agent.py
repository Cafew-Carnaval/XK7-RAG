import re
import json
import unicodedata
import numpy as np
import pandas as pd
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from sql_schema import connect, get_schema, DB_PATH
from sql_agent import run_sql, UnsafeQuery

load_dotenv()

# Le modèle ne sert qu'à proposer des règles métier à partir de la STRUCTURE : il ne voit aucune donnée
rules_llm = ChatOpenAI(model="gpt-4o", temperature=0,
                       model_kwargs={"response_format": {"type": "json_object"}})
RULES_LOG = []   # tout ce qui est envoyé au modèle, pour vérifier la confidentialité

ERREUR, A_VERIFIER, INFO = "erreur", "à vérifier", "info"
MAX_EXAMPLES = 5

RULES_PROMPT = """Tu es expert en qualité des données. Voici la STRUCTURE d'une base (tables, colonnes, types, liens),
sans aucune donnée, et la demande de l'utilisateur.

1. Indique quelles tables la demande concerne (liste vide si elle concerne toute la base).
2. Propose au maximum 5 règles métier de cohérence, vérifiables en SQL, qui ont du sens pour cette base :
   cohérence entre dates (ex. une commande ne peut pas précéder l'inscription du client), entre montants
   (ex. un total égal à la somme de ses lignes), valeurs impossibles (ex. un prix négatif ou nul), etc.
   Pour chaque règle, écris une requête SQLite SELECT qui renvoie les lignes qui VIOLENT la règle, en
   sélectionnant d'abord les colonnes qui identifient la ligne. N'invente aucune valeur de données.
   Utilise exactement les noms de la structure, entre guillemets doubles s'ils contiennent des espaces
   ou des accents. Les dates sont des textes 'AAAA-MM-JJ HH:MM:SS'.
   Ne propose pas de règle sur les valeurs manquantes, les doublons, les formats ou les liens entre tables :
   ils sont déjà contrôlés par ailleurs.

Structure :
{schema}

Demande : {question}

Réponds uniquement avec un JSON :
{{"tables": ["..."], "regles": [{{"regle": "phrase courte", "table": "...", "sql": "SELECT ..."}}]}}"""


FIX_PROMPT = """Cette requête SQLite, qui doit renvoyer les lignes qui violent la règle « {rule} », a échoué.

Structure de la base :
{schema}

Requête :
{sql}

Erreur : {error}

Corrige la requête (noms exacts de la structure, entre guillemets doubles s'ils contiennent des espaces, des
accents ou une apostrophe, sans commentaire). Réponds uniquement avec un JSON : {{"sql": "SELECT ..."}}"""


# --- Outils ----------------------------------------------------------------------------------

def plain(value):
    """Texte simplifié pour comparer : minuscules, sans accents, sans ponctuation ni espaces en trop."""
    text = unicodedata.normalize("NFKD", str(value)).encode("ascii", "ignore").decode().lower()
    return " ".join(re.sub(r"[^\w]+", " ", text).split())


def shape(value):
    """Forme d'une valeur : lettres -> A, chiffres -> 9 ("C042" -> "A999", "75001" -> "99999")."""
    return re.sub(r"[0-9]", "9", re.sub(r"[^\W\d_]", "A", str(value)))


def as_key(series):
    """Valeurs comparables entre tables : 1.0 et 1 deviennent "1"."""
    def one(v):
        if isinstance(v, float) and v.is_integer():
            return str(int(v))
        return str(v).strip()
    return series.map(one)


def has_letters(series, share=0.8):
    """Vrai si la plupart des valeurs contiennent des lettres (noms, emails...), pas des dates ou des nombres."""
    text = series.dropna().astype(str)
    return len(text) > 0 and text.str.contains(r"[^\W\d_]{2,}").mean() >= share


def is_empty(series):
    return series.isna() | series.astype(str).str.strip().isin(["", "nan", "None", "NaT"])


def is_id_column(name):
    return bool(re.search(r"(^|[\s_])(id|n°|no|num|numero|numéro|matricule|code|ref|réf|référence|cle|clé|siret|siren)([\s_]|$)|id$", name.lower()))


def issue(kind, table, column, gravity, detail, rows, df, examples_cols=None):
    """Un problème trouvé : type, table, colonne, gravité, explication, lignes concernées et exemples.
    Les exemples restent sur la machine : ils ne sont jamais envoyés au modèle."""
    rows = list(rows)
    cols = examples_cols or list(df.columns[:3]) + ([column] if column and column not in df.columns[:3] else [])
    examples = df.loc[rows[:MAX_EXAMPLES], [c for c in cols if c in df.columns]].to_dict("records")
    return {"type": kind, "table": table, "colonne": column, "gravite": gravity, "detail": detail,
            "nb": len(rows), "lignes": rows, "exemples": examples}


# --- Lecture de la base -----------------------------------------------------------------------

def load_tables(db_path):
    """Charge chaque table, ses clés primaires déclarées et ses liens déclarés."""
    conn = connect(db_path)
    try:
        names = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        tables, pks, fks = {}, {}, []
        for t in names:
            tables[t] = pd.read_sql(f'SELECT * FROM "{t}"', conn)
            pks[t] = [c[1] for c in conn.execute(f'PRAGMA table_info("{t}")') if c[5]]
            fks += [(t, fk[3], fk[2], fk[4]) for fk in conn.execute(f'PRAGMA foreign_key_list("{t}")')]
        return tables, pks, fks
    finally:
        conn.close()


def infer_pk(df, declared):
    """Clé primaire : celle déclarée, sinon une colonne identifiant aux valeurs toutes différentes (cas d'un Excel)."""
    if declared:
        return declared
    for col in df.columns:
        values = df[col]
        if is_id_column(col) and not is_empty(values).any() and values.is_unique:
            return [col]
    return []


# --- Les contrôles (en local, sans IA) ----------------------------------------------------------

def check_missing(t, df, pk):
    found = []
    for col in df.columns:
        empty = is_empty(df[col])
        n, rate = int(empty.sum()), empty.mean()
        if n == 0:
            continue
        if rate > 0.2:   # colonne souvent vide : probablement facultative
            found.append(issue("manquant", t, col, INFO, f"{rate:.0%} de valeurs vides : colonne probablement "
                               "facultative", df.index[empty], df))
        else:            # quelques trous dans une colonne presque toujours remplie : suspect
            found.append(issue("manquant", t, col, A_VERIFIER, f"{n} valeur(s) vide(s) dans une colonne "
                               f"remplie à {1 - rate:.0%}", df.index[empty], df))
    return found


def check_duplicates(t, df, pk):
    found = []
    others = [c for c in df.columns if c not in pk]
    if not others:
        return found
    # Doublons exacts : mêmes valeurs partout, sauf l'identifiant (ex. une commande saisie deux fois)
    exact = df.duplicated(subset=others, keep="first")
    if exact.any():
        found.append(issue("doublon", t, None, ERREUR, f"{int(exact.sum())} ligne(s) identique(s) à une autre "
                           "(hors identifiant)", df.index[exact], df))
    # Doublons approchés : même valeur après simplification (majuscules, accents, espaces) mais écrite
    # différemment ("Dupont" / "DUPONT "), sur les colonnes texte qui identifient une ligne (nom, email...)
    text_cols = [c for c in others if df[c].dtype == object or pd.api.types.is_string_dtype(df[c])]
    identity = [c for c in text_cols if not is_id_column(c) and has_letters(df[c])
                and df[c].nunique() >= 0.5 * len(df)]
    if identity and len(df) > 1:
        ident = df[identity].fillna("").astype(object).map(str)
        raw = ident.agg(" | ".join, axis=1)
        key = ident.map(plain).agg(" | ".join, axis=1)
        variants = raw.groupby(key).transform("nunique")   # nombre d'écritures différentes de la même clé
        near = (variants > 1) & ~df.duplicated(subset=others, keep=False)
        # Contexte : une colonne de catégories qui diffère entre les lignes semblables (ex. le département de deux
        # communes homonymes, Saint-Rémy dans l'Ain et Saint-Rémy en Saône-et-Loire) montre qu'il s'agit de deux
        # choses différentes, pas d'une saisie en double
        context = [c for c in text_cols if c not in identity and not is_id_column(c)
                   and 1 < df[c].nunique() <= 0.5 * len(df)]
        for c in context:
            differs = df[c].fillna("").astype(str).map(plain).groupby(key).transform("nunique") > 1
            near &= ~differs
        if near.any():
            found.append(issue("doublon approché", t, ", ".join(identity), A_VERIFIER,
                               f"{int(near.sum())} ligne(s) qui se ressemblent après simplification "
                               f"({', '.join(identity)}) : même personne ou même produit saisi deux fois ?",
                               df.index[near], df))
    return found


def check_formats(t, df, pk):
    found = []
    for col in df.columns:
        values = df[col][~is_empty(df[col])]
        if values.empty or pd.api.types.is_numeric_dtype(values):
            continue
        text = values.astype(str)
        # Espaces en trop au début ou à la fin
        spaces = text != text.str.strip()
        if spaces.any():
            found.append(issue("format", t, col, ERREUR, f"{int(spaces.sum())} valeur(s) avec des espaces en trop",
                               text.index[spaces], df))
        # Emails invalides
        if "mail" in col.lower():
            bad = ~text.str.strip().str.match(r"^[^@\s]+@[^@\s]+\.[a-zA-Z]{2,}$")
            if bad.any():
                found.append(issue("format", t, col, ERREUR, f"{int(bad.sum())} email(s) invalide(s)",
                                   text.index[bad], df))
            continue
        # Formes incohérentes : si presque toutes les valeurs ont la même forme (ex. 5 chiffres), les autres sont suspectes
        shapes = text.str.strip().map(shape)
        top, share = shapes.value_counts().index[0], shapes.value_counts(normalize=True).iloc[0]
        if len(text) >= 10 and 0.9 <= share < 1:
            bad = shapes != top
            other_shapes = shapes[bad].value_counts()
            if other_shapes.min() >= 20:
                # Chaque autre format revient souvent : sans doute une convention (ex. 2A pour la Corse, 971 pour
                # la Guadeloupe), pas une faute de frappe, qui serait isolée
                found.append(issue("format", t, col, INFO, f"plusieurs formats coexistent : {top} et "
                                   f"{', '.join(other_shapes.index)} ({int(bad.sum())} valeurs). Si c'est une "
                                   "convention connue, rien à faire", text.index[bad], df))
            else:
                found.append(issue("format", t, col, A_VERIFIER, f"{int(bad.sum())} valeur(s) au format différent "
                                   f"des autres (format habituel : {top})", text.index[bad], df))
        # Même valeur écrite de plusieurs façons ("Lyon", "lyon", "LYON ") dans une colonne de catégories
        if text.nunique() <= 50 and text.nunique() <= 0.5 * len(text):
            for _, group in text.groupby(text.map(plain)):
                counts = group.value_counts()
                if len(counts) > 1:
                    main = counts.index[0]
                    bad = text.map(plain).eq(plain(main)) & text.ne(main)
                    found.append(issue("format", t, col, ERREUR, f"« {main} » écrit de {len(counts)} façons "
                                       "différentes", text.index[bad], df))
    return found


def check_outliers(t, df, pk):
    found = []
    for col in df.columns:
        if col in pk or is_id_column(col) or not pd.api.types.is_numeric_dtype(df[col]):
            continue
        values = df[col].dropna()
        if len(values) < 10:
            continue
        # Négatifs dans une colonne presque toujours positive (ex. une quantité de -2)
        negative = values < 0
        if negative.any() and negative.mean() < 0.05:
            found.append(issue("aberrant", t, col, ERREUR, f"{int(negative.sum())} valeur(s) négative(s) dans une "
                               "colonne normalement positive", values.index[negative], df))
        # Valeurs extrêmes (règle de l'écart interquartile, version large : 3 x IQR). Pour des montants positifs
        # très asymétriques (quelques valeurs bien plus hautes, comme les loyers de Paris), l'écart est mesuré
        # sur une échelle logarithmique : un loyer 3 fois plus cher que la médiane est rare mais normal
        q1, q3 = values.quantile(0.25), values.quantile(0.75)
        iqr = q3 - q1
        skewed = values.min() > 0 and values.skew() > 1
        if skewed:
            logs = np.log(values)
            l1, l3 = logs.quantile(0.25), logs.quantile(0.75)
            extreme = (logs > l3 + 3 * (l3 - l1)) | (logs < l1 - 3 * (l3 - l1)) if l3 > l1 else logs < -np.inf
        elif iqr > 0:
            extreme = (values > q3 + 3 * iqr) | ((values < q1 - 3 * iqr) & (values >= 0))
        if iqr > 0:
            # Au-delà de 2 % des valeurs (et de 2 lignes), ce n'est plus une anomalie mais un groupe à part
            # (ex. des films parmi des chansons)
            # Quelques valeurs isolées (1 à 3, ex. un salaire de 48 000 €) : sans doute une erreur de saisie.
            # Des dizaines de valeurs extrêmes : la queue naturelle de la distribution (ex. les loyers de Paris)
            if extreme.any() and extreme.sum() <= max(2, 0.02 * len(values)) and extreme.sum() >= 20:
                found.append(issue("aberrant", t, col, INFO, f"{int(extreme.sum())} valeurs bien plus élevées que les "
                                   "autres, trop nombreuses pour être des fautes de frappe : sans doute des cas réels "
                                   f"(ex. les grandes villes). Habituellement entre {q1:g} et {q3:g}",
                                   values.index[extreme], df))
            elif extreme.any() and extreme.sum() <= max(2, 0.02 * len(values)):
                found.append(issue("aberrant", t, col, A_VERIFIER, f"{int(extreme.sum())} valeur(s) très éloignée(s) "
                                   f"des autres (habituellement entre {q1:g} et {q3:g})", values.index[extreme], df))
    return found


def check_links(tables, pks, fks):
    """Liens cassés : une valeur qui pointe vers une ligne inexistante (ex. une commande d'un client inconnu).
    Liens déclarés (clés étrangères) + liens devinés : même nom de colonne, identifiant dans l'autre table."""
    links = set(fks)
    for parent, ppk in pks.items():
        if len(ppk) != 1:
            continue
        for child, df in tables.items():
            if child != parent and ppk[0] in df.columns and ppk[0] not in pks[child]:
                links.add((child, ppk[0], parent, ppk[0]))
    found = []
    for child, col, parent, pcol in sorted(links):
        if child not in tables or parent not in tables or col not in tables[child] or pcol not in tables[parent]:
            continue
        df = tables[child]
        known = set(as_key(tables[parent][pcol].dropna()))
        orphan = ~is_empty(df[col]) & ~as_key(df[col]).isin(known)
        if orphan.any():
            found.append(issue("lien cassé", child, col, ERREUR, f"{int(orphan.sum())} valeur(s) de {col} absente(s) "
                               f"de la table {parent}", df.index[orphan], df))
    return found


# --- Les règles métier (proposées par le modèle à partir de la structure) ----------------------------

def clean_rule_sql(sql):
    """Répare les petits défauts fréquents des requêtes écrites par le modèle."""
    sql = re.sub(r"--[^\n]*", " ", str(sql))                      # commentaires de fin de ligne
    # Apostrophe doublée dans un nom de colonne : "Date d''inscription" -> "Date d'inscription"
    sql = re.sub(r'"([^"]*)"', lambda m: '"' + m.group(1).replace("''", "'") + '"', sql)
    return sql.strip().rstrip(";").strip()


def run_rule(sql, db_path):
    """Exécute une règle : nombre de lignes en infraction et quelques exemples."""
    count = run_sql(f"SELECT COUNT(*) FROM (\n{sql}\n)", db_path)[1][0][0]
    columns, rows, _ = run_sql(sql, db_path, max_rows=MAX_EXAMPLES)
    return count, columns, rows


def check_rules(question, db_path, schema):
    """Le modèle propose des règles en SQL à partir de la structure. Chaque requête passe par les barrières
    de sécurité de XK7-SQL et s'exécute en local : seul le nombre de violations est affiché, rien ne repart."""
    prompt = RULES_PROMPT.format(schema=schema, question=question)
    RULES_LOG.append(prompt)
    try:
        answer = json.loads(rules_llm.invoke(prompt).content)
    except (ValueError, TypeError):
        return [], [], []
    focus = [t for t in answer.get("tables", []) if isinstance(t, str)]
    results, found = [], []
    for rule in answer.get("regles", [])[:5]:
        sql = clean_rule_sql(rule.get("sql", ""))
        try:
            try:
                count, columns, rows = run_rule(sql, db_path)
            except UnsafeQuery:
                raise
            except Exception as first_error:
                # Auto-correction : une seule tentative, avec le message d'erreur (qui ne contient que la structure)
                fix = FIX_PROMPT.format(rule=rule.get("regle", ""), schema=schema, sql=sql, error=first_error)
                RULES_LOG.append(fix)
                sql = clean_rule_sql(json.loads(rules_llm.invoke(fix).content).get("sql", ""))
                count, columns, rows = run_rule(sql, db_path)
        except UnsafeQuery as e:                # requête dangereuse : refusée par les barrières de XK7-SQL
            results.append({"regle": rule.get("regle", ""), "sql": sql, "statut": "refusée par la sécurité",
                            "erreur": str(e)})
            continue
        except Exception as e:                  # règle mal écrite : on la signale sans planter
            results.append({"regle": rule.get("regle", ""), "sql": sql, "statut": "non vérifiable", "erreur": str(e)})
            continue
        results.append({"regle": rule.get("regle", ""), "sql": sql, "statut": "ok" if count == 0 else "violée",
                        "nb": count})
        if count:
            found.append({"type": "règle métier", "table": rule.get("table", ""), "colonne": None,
                          "gravite": ERREUR, "detail": f"{rule.get('regle', '')} : {count} ligne(s) en infraction",
                          "nb": count, "lignes": [], "exemples": [dict(zip(columns, r)) for r in rows],
                          "sql": sql})
    return focus, results, found


# --- Les propositions de correction (écrites par le code, jamais exécutées) ----------------------------

def q_name(name):
    """Nom de table ou de colonne protégé pour SQL : "Date d'inscription"."""
    return '"' + str(name).replace('"', '""') + '"'


def q_value(value):
    """Valeur protégée pour SQL : 'O''Reilly'."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(int(value)) if float(value).is_integer() else str(value)
    return "'" + str(value).replace("'", "''") + "'"


def propose_fix(i, df, pk):
    """Propose une correction pour un problème : une instruction SQL quand la correction est sûre et
    mécanique, sinon une requête pour retrouver les lignes et un conseil. Rien n'est jamais exécuté."""
    t, col, kind = q_name(i["table"]), i.get("colonne"), i["type"]
    key = pk[0] if len(pk) == 1 else None
    ids = [df.loc[r, key] for r in i["lignes"] if r in df.index] if key else []
    where_ids = f"{q_name(key)} IN ({', '.join(q_value(v) for v in ids)})" if ids else None
    listing = f"SELECT * FROM {t} WHERE {where_ids};" if where_ids else None

    if kind == "format" and "espaces" in i["detail"]:
        c = q_name(col)
        return {"sql": f"UPDATE {t} SET {c} = TRIM({c}) WHERE {c} <> TRIM({c});",
                "conseil": "Retire les espaces en trop."}
    if kind == "format" and "façons différentes" in i["detail"]:
        main = re.search(r"« (.*) »", i["detail"]).group(1)
        variants = sorted({str(v) for v in df.loc[i["lignes"], col] if str(v) != main})
        c = q_name(col)
        return {"sql": f"UPDATE {t} SET {c} = {q_value(main)} WHERE {c} IN ({', '.join(q_value(v) for v in variants)});",
                "conseil": f"Harmonise l'écriture sur « {main} »."}
    if kind == "doublon" and where_ids:
        return {"sql": f"DELETE FROM {t} WHERE {where_ids};",
                "conseil": "Supprime les copies (la première ligne de chaque doublon est conservée)."}
    if kind == "doublon approché":
        return {"sql": listing, "conseil": "Vérifie s'il s'agit de la même personne ou du même produit ; si oui, "
                "garde une seule fiche et rattache-lui les lignes liées (commandes, absences...) avant de supprimer l'autre."}
    if kind == "lien cassé":
        return {"sql": listing, "conseil": "Corrige la valeur si c'est une faute de frappe, ou crée la ligne manquante "
                "dans la table visée."}
    if kind == "aberrant":
        return {"sql": listing, "conseil": "Vérifie la saisie : signe inversé, unité différente (ex. un montant annuel "
                "saisi au lieu de mensuel), zéro en trop."}
    if kind == "manquant":
        return {"sql": listing, "conseil": "Complète les valeurs manquantes à partir de la source (formulaire, dossier)."}
    if kind == "règle métier":
        return {"sql": i.get("sql", "") + ";", "conseil": "Corrige ces lignes à la main : seule une personne peut savoir "
                "quelle valeur est la bonne."}
    return {"sql": listing, "conseil": "Corrige ces valeurs à la main."}


def correction_script(report):
    """Assemble toutes les corrections en un script SQL à relire, dans une transaction annulable."""
    lines = ["-- Script de correction proposé par XK7-QUALITE.",
             "-- XK7 ne l'exécute jamais : relis-le, adapte-le, puis lance-le toi-même sur une copie de la base.",
             "BEGIN TRANSACTION;", ""]
    for t, r in report["tables"].items():
        for i in r["problemes"]:
            fix = i.get("correction")
            if not fix or i["gravite"] == INFO:
                continue
            lines.append(f"-- {t} · {i['type']}{' · ' + i['colonne'] if i.get('colonne') else ''} : {i['detail']}")
            lines.append(f"-- {fix['conseil']}")
            if fix.get("sql"):
                statement = fix["sql"]
                # Les requêtes de consultation sont mises en commentaire : seules les corrections sûres sont actives
                if statement.lstrip().upper().startswith("SELECT"):
                    statement = "-- " + statement
                lines.append(statement)
            lines.append("")
    lines += ["-- Vérifie le résultat, puis valide avec COMMIT; ou annule tout avec ROLLBACK;", "COMMIT;"]
    return "\n".join(lines)


# --- Le point d'entrée -------------------------------------------------------------------------

def check_quality(question="Vérifie la qualité des données.", db_path=DB_PATH, use_rules=True):
    """XK7-QUALITE : contrôle la base et renvoie un rapport avec un score par table."""
    tables, declared, fks = load_tables(db_path)
    pks = {t: infer_pk(df, declared[t]) for t, df in tables.items()}

    issues = []
    for t, df in tables.items():
        for check in (check_missing, check_duplicates, check_formats, check_outliers):
            issues += check(t, df, pks[t])
    issues += check_links(tables, pks, fks)

    for i in issues:   # une proposition de correction pour chaque problème
        if i["table"] in tables:
            i["correction"] = propose_fix(i, tables[i["table"]], pks[i["table"]])

    focus, rules = [], []
    if use_rules:
        focus, rules, rule_issues = check_rules(question, db_path, get_schema(db_path))
        for i in rule_issues:
            i["correction"] = propose_fix(i, tables.get(i["table"], pd.DataFrame()), pks.get(i["table"], []))
        issues += rule_issues

    # Score par table : part des lignes sans erreur ni point à vérifier (les "info" ne comptent pas)
    report = {}
    for t, df in tables.items():
        t_issues = [i for i in issues if i["table"] == t]
        flagged = set()
        for i in t_issues:
            if i["gravite"] != INFO:
                flagged.update(i["lignes"])
        # Plusieurs règles peuvent viser les mêmes lignes : on compte la règle la plus touchée (estimation prudente)
        rule_rows = max([i["nb"] for i in t_issues if i["type"] == "règle métier"], default=0)
        bad = min(len(df), len(flagged) + rule_rows)
        score = 100 if len(df) == 0 else round(100 * (1 - bad / len(df)))
        report[t] = {"lignes": len(df), "score": score, "cle": pks[t], "problemes": t_issues}

    shown = {t: r for t, r in report.items() if not focus or t in focus} or report
    total_rows = sum(r["lignes"] for r in shown.values())
    global_score = round(sum(r["score"] * r["lignes"] for r in shown.values()) / total_rows) if total_rows else 100
    result = {"score": global_score, "tables": shown, "regles": rules, "focus": focus}
    result["script"] = correction_script(result)
    return result


def summary(report):
    """Résumé en quelques lignes, écrit par le code (sans IA)."""
    lines = [f"Score de qualité : {report['score']}/100"]
    for t, r in sorted(report["tables"].items(), key=lambda x: x[1]["score"]):
        counts = {}
        for i in r["problemes"]:
            if i["gravite"] != INFO:
                counts[i["type"]] = counts.get(i["type"], 0) + i["nb"]
        detail = ", ".join(f"{n} {k}" for k, n in counts.items()) or "aucun problème"
        lines.append(f"- {t} ({r['lignes']} lignes) : {r['score']}/100, {detail}")
    return "\n".join(lines)


if __name__ == "__main__":
    import sys
    path = sys.argv[1] if len(sys.argv) > 1 else DB_PATH
    if not path.endswith((".db", ".sqlite", ".sqlite3")):
        from data_upload import to_sqlite, delete_file
        with open(path, "rb") as f:
            db = to_sqlite(path, f.read())
    else:
        db, delete_file = path, None
    rep = check_quality(db_path=db)
    print(summary(rep))
    for t, r in rep["tables"].items():
        for i in r["problemes"]:
            print(f"\n[{i['gravite']}] {t} · {i['type']} · {i['colonne'] or ''}\n  {i['detail']}")
            for e in i["exemples"][:3]:
                print("   ", e)
    print("\nRègles métier proposées :")
    for r in rep["regles"]:
        print(f"- {r['regle']} : {r['statut']}" + (f" ({r['nb']} lignes)" if r.get("nb") else ""))
    if delete_file:
        delete_file(db)
