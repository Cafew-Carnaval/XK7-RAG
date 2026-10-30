"""API de XK7 : la même intelligence que l'application, pour d'autres programmes.

    uvicorn api:app --reload        -> http://localhost:8000/docs (documentation et essais dans le navigateur)

Routes :
    GET  /health        l'API est-elle en vie ?
    GET  /bases         les bases de démonstration disponibles
    POST /ask           une question sur une base de démonstration  -> réponse de XK7-CORE en JSON
    POST /ask-file      une question sur un fichier envoyé (Excel, CSV, SQLite)

Sécurité : si XK7_API_KEY est défini (fichier .env ou variable d'environnement), chaque appel doit envoyer
l'en-tête « X-API-Key » avec cette valeur. Chaque question appelle l'API OpenAI, donc coûte : pas d'accès libre.
Confidentialité : identique à l'application. Les données restent sur le serveur ; les modèles ne voient que
la structure et la question.
"""
import math
import os
import time
from datetime import date, datetime
from pathlib import Path
from typing import Literal, Optional

import numpy as np
import pandas as pd
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, File, Form, HTTPException, Security, UploadFile
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, Field

load_dotenv()

from core import run                      # noqa: E402  (après load_dotenv : la clé OpenAI doit être chargée)
from data_upload import to_sqlite, UploadError   # noqa: E402
from sql_schema import DB_PATH            # noqa: E402

DATA = Path(__file__).parent / "data"
BASES = {
    "chinook": (DB_PATH, "la base de démonstration Chinook", "Magasin de musique (clients, factures, morceaux)"),
    "immobilier": (DATA / "ventes_immobilier_44.xlsx", None, "Ventes immobilières en Loire-Atlantique, 2021-2025 (DVF)"),
    "cafe": (DATA / "ventes_cafe_test.xlsx", None, "Ventes d'un café (fictif)"),
}
MAX_ROWS = 200          # lignes de résultat renvoyées au plus (le reste est résumé)
MAX_QUESTION = 500      # caractères

app = FastAPI(title="XK7", version="1.0",
              description="Assistant data multi-agent : cours SQL (RAG), requêtes, qualité, analyse, prévision. "
                          "XK7-CORE choisit l'agent. Les données ne sont jamais montrées aux modèles.")
_converted = {}         # bases Excel déjà converties en SQLite (on ne refait pas la conversion à chaque appel)


# --- Sécurité ----------------------------------------------------------------------------

# Déclarée comme schéma de sécurité : la page /docs affiche un bouton « Authorize » pour la saisir une fois
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False, description="La valeur de XK7_API_KEY")


def check_key(x_api_key: Optional[str] = Security(api_key_header)):
    expected = os.getenv("XK7_API_KEY")
    if expected and x_api_key != expected:
        raise HTTPException(status_code=401, detail="Clé API absente ou invalide (en-tête X-API-Key).")


# --- Mise en JSON : les agents renvoient des tableaux pandas, des nombres numpy, des fonctions... -------

def to_json(value, depth=0):
    """Transforme n'importe quel résultat d'agent en JSON propre. Les clés « _ » (objets internes, comme le
    modèle entraîné) et les fonctions sont retirées ; les tableaux sont coupés à MAX_ROWS lignes."""
    if depth > 8:
        return str(value)
    if value is None or isinstance(value, (bool, str)):
        return value
    if isinstance(value, (int, np.integer)):
        return int(value)
    if isinstance(value, (float, np.floating)):
        return None if math.isnan(value) or math.isinf(value) else round(float(value), 6)
    if isinstance(value, (pd.Timestamp, datetime, date)):
        return value.isoformat()
    if isinstance(value, pd.Period):
        return str(value)
    if isinstance(value, pd.DataFrame):
        rows = value.head(MAX_ROWS)
        if not isinstance(rows.index, pd.RangeIndex):      # index porteur de sens (dates, catégories) : gardé
            rows = rows.reset_index()
        return {"colonnes": [str(c) for c in rows.columns],
                "lignes": [[to_json(v, depth + 1) for v in r] for r in rows.itertuples(index=False)],
                "total_lignes": len(value), "coupe": len(value) > MAX_ROWS}
    if isinstance(value, pd.Series):
        return {str(k): to_json(v, depth + 1) for k, v in value.head(MAX_ROWS).items()}
    if isinstance(value, np.ndarray):
        return [to_json(v, depth + 1) for v in value[:MAX_ROWS].tolist()]
    if isinstance(value, dict):
        return {str(k): to_json(v, depth + 1) for k, v in value.items()
                if not str(k).startswith("_") and not callable(v)}
    if isinstance(value, (list, tuple, set)):
        return [to_json(v, depth + 1) for v in list(value)[:MAX_ROWS] if not callable(v)]
    if hasattr(value, "page_content"):          # passage de document (RAG)
        return {"texte": value.page_content, "source": to_json(getattr(value, "metadata", {}), depth + 1)}
    if hasattr(value, "to_dict"):                # graphique Altair : la spécification Vega-Lite
        try:
            return {"graphique_vega_lite": value.to_dict()}
        except Exception:  # noqa: BLE001
            return None
    return str(value)


def tidy(agent, result):
    """Allège ce qui ne sert qu'à l'interface : les prix des cas semblables (pour l'histogramme) deviennent un
    résumé, et les listes de valeurs possibles (pour les menus du simulateur) sont retirées."""
    if agent == "prevision" and isinstance(result, dict) and result.get("type") == "caracteristiques":
        similar = result.get("semblables")
        if isinstance(similar, pd.Series) and len(similar):
            result = {**result, "semblables": {
                "nombre": int(similar.size), "mediane": float(similar.median()),
                "quartile_bas": float(similar.quantile(0.25)), "quartile_haut": float(similar.quantile(0.75))}}
        result = {k: v for k, v in result.items() if k != "options"}
    return result


def answer(question, history, db_path, source, level, k):
    if not question.strip():
        raise HTTPException(status_code=422, detail="Question vide.")
    if len(question) > MAX_QUESTION:
        raise HTTPException(status_code=422, detail=f"Question trop longue (maximum {MAX_QUESTION} caractères).")
    start = time.time()
    state = run(question, history or [], str(db_path), level, k, source)
    agents = ("rag", "sql", "qualite", "analyse", "prevision")
    return {
        "question": question,
        "agent": state.get("agent"),
        "raison": state.get("reason"),
        "question_comprise": state.get("standalone"),
        "message": state.get("message"),
        "reponses": {a: to_json(tidy(a, state[a])) for a in agents if state.get(a) is not None},
        "duree_s": round(time.time() - start, 2),
    }


# --- Routes ------------------------------------------------------------------------------

class Message(BaseModel):
    role: Literal["user", "assistant"]
    content: str = ""
    agent: Optional[str] = None


class Question(BaseModel):
    # Exemple prérempli dans /docs : directement utilisable
    model_config = {"json_schema_extra": {"examples": [
        {"question": "Combien vaut un T3 de 65 m² à Rezé ?", "base": "immobilier"}]}}

    question: str
    base: Literal["chinook", "immobilier", "cafe"] = "chinook"
    historique: list[Message] = Field(default_factory=list,
                                      description="Échanges précédents, pour une question de suite (« Et à Nantes ? »)")
    niveau: Literal["Débutant", "Intermédiaire", "Expert"] = "Intermédiaire"


@app.get("/health")
def health():
    return {"statut": "ok", "cle_openai": bool(os.getenv("OPENAI_API_KEY"))}


@app.get("/bases")
def bases():
    return {name: info[2] for name, info in BASES.items()}


def base_path(name):
    path, source, _ = BASES[name]
    if source:                                   # base SQLite prête à l'emploi
        return path, source
    if name not in _converted:                   # fichier Excel : converti une fois en SQLite
        _converted[name] = to_sqlite(path.name, path.read_bytes())
    return _converted[name], f"le fichier déposé par l'utilisateur ({path.name})"


@app.post("/ask", dependencies=[Depends(check_key)])
def ask(q: Question):
    db_path, source = base_path(q.base)
    return answer(q.question, [m.model_dump() for m in q.historique], db_path, source, q.niveau, 3)


@app.post("/ask-file", dependencies=[Depends(check_key)])
async def ask_file(question: str = Form(...), fichier: UploadFile = File(...)):
    """Pose une question sur ton propre fichier (Excel, CSV ou SQLite, 10 Mo au plus). Le fichier est converti
    en base temporaire sur le serveur ; son contenu n'est jamais envoyé aux modèles."""
    try:
        db_path = to_sqlite(fichier.filename, await fichier.read())
    except UploadError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return answer(question, [], db_path, f"le fichier déposé par l'utilisateur ({fichier.filename})",
                  "Intermédiaire", 3)
