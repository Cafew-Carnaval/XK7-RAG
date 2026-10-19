import os
import re
import time
import uuid
import sqlite3
import tempfile
import pandas as pd

MAX_SIZE_MB = 10
TEMP_PREFIX = "xk7_upload_"
MAX_AGE_SECONDS = 3600   # les fichiers temporaires de plus d'une heure sont supprimés


class UploadError(Exception):
    """Fichier refusé (trop gros, format inconnu, illisible)."""


def table_name(name):
    """Nom de table simple à partir d'un nom de fichier ou d'onglet : "Ventes 2024.csv" -> "Ventes_2024"."""
    name = re.sub(r"\W+", "_", os.path.splitext(name)[0]).strip("_")
    return name or "donnees"


def fix_numbers(df):
    """Convertit en nombres les colonnes de texte qui contiennent des nombres à la française ("12,5")."""
    for col in df.columns:
        if pd.api.types.is_object_dtype(df[col]) or pd.api.types.is_string_dtype(df[col]):
            values = df[col].dropna().astype(str).str.replace(" ", "").str.replace(" ", "")
            converted = pd.to_numeric(values.str.replace(",", "."), errors="coerce")
            if len(values) and converted.notna().all():
                df[col] = pd.to_numeric(
                    df[col].astype(str).str.replace(" ", "").str.replace(" ", "").str.replace(",", "."),
                    errors="coerce")
    return df


def cleanup_old_files():
    """Supprime les bases temporaires oubliées depuis plus d'une heure."""
    folder = tempfile.gettempdir()
    for f in os.listdir(folder):
        path = os.path.join(folder, f)
        if f.startswith(TEMP_PREFIX) and time.time() - os.path.getmtime(path) > MAX_AGE_SECONDS:
            try:
                os.remove(path)
            except OSError:
                pass


def delete_file(path):
    """Supprime une base temporaire."""
    if path and os.path.basename(path).startswith(TEMP_PREFIX) and os.path.exists(path):
        os.remove(path)


def to_sqlite(filename, content):
    """Transforme un fichier déposé (SQLite, CSV ou Excel) en base SQLite temporaire.
    Renvoie le chemin de la base. Le contenu n'est jamais envoyé au modèle."""
    if len(content) > MAX_SIZE_MB * 1024 * 1024:
        raise UploadError(f"Fichier trop gros (maximum {MAX_SIZE_MB} Mo).")

    cleanup_old_files()
    path = os.path.join(tempfile.gettempdir(), f"{TEMP_PREFIX}{uuid.uuid4().hex}.db")
    ext = os.path.splitext(filename)[1].lower()

    try:
        if ext in (".db", ".sqlite", ".sqlite3"):
            if not content.startswith(b"SQLite format 3"):
                raise UploadError("Ce fichier n'est pas une base SQLite.")
            with open(path, "wb") as f:
                f.write(content)
        elif ext in (".csv", ".xlsx"):
            if ext == ".csv":
                import io
                # sep=None : détecte tout seul le séparateur (virgule, point-virgule...)
                text = content.decode("utf-8-sig", errors="replace")
                sheets = {table_name(filename): pd.read_csv(io.StringIO(text), sep=None, engine="python")}
            else:
                import io
                sheets = {table_name(name): df for name, df in
                          pd.read_excel(io.BytesIO(content), sheet_name=None).items()}
            conn = sqlite3.connect(path)
            for name, df in sheets.items():
                df.columns = [str(c).strip() for c in df.columns]
                df = fix_numbers(df)
                df.to_sql(name, conn, index=False)
            conn.close()
        else:
            raise UploadError("Format non pris en charge : utilise un fichier .csv, .xlsx, .db ou .sqlite.")

        # Vérifie que la base s'ouvre et contient au moins une table
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        tables = conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table'").fetchone()[0]
        conn.close()
        if tables == 0:
            raise UploadError("Le fichier ne contient aucune table.")
        return path
    except UploadError:
        delete_file(path)
        raise
    except Exception as e:
        delete_file(path)
        raise UploadError(f"Impossible de lire ce fichier ({type(e).__name__}).")
