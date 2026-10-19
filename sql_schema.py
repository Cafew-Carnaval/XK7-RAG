import sqlite3

DB_PATH = "data/chinook.db"


def connect(db_path=DB_PATH):
    """Ouvre la base en lecture seule : aucune requête ne peut la modifier."""
    return sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)


def get_schema(db_path=DB_PATH, sample_rows=0):
    """Décrit la STRUCTURE de la base en un texte court pour le modèle : une ligne par table
    avec ses colonnes, sa clé primaire et ses liens.

    Confidentialité : par défaut (sample_rows=0), aucune donnée n'est incluse, seulement la structure.
    sample_rows > 0 ajoute des lignes d'exemple : à réserver aux bases de démonstration.

    Exemple de résultat :
    Album (347 lignes) : AlbumId INTEGER clé primaire, Title NVARCHAR(160), ArtistId INTEGER -> Artist.ArtistId
    """
    conn = connect(db_path)
    cur = conn.cursor()
    tables = [row[0] for row in cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    )]

    lines = []
    for table in tables:
        # Liens de la table : colonne locale -> table.colonne visée
        links = {fk[3]: f"{fk[2]}.{fk[4]}" for fk in cur.execute(f'PRAGMA foreign_key_list("{table}")')}

        columns = []
        for _, name, col_type, _, _, pk in cur.execute(f'PRAGMA table_info("{table}")'):
            desc = f"{name} {col_type}"
            if pk:
                desc += " clé primaire"
            if name in links:
                desc += f" -> {links[name]}"
            columns.append(desc)

        count = cur.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
        lines.append(f"{table} ({count} lignes) : {', '.join(columns)}")

        # Lignes d'exemple, seulement si on les demande (jamais pour des données confidentielles)
        if sample_rows > 0:
            for row in cur.execute(f'SELECT * FROM "{table}" LIMIT {sample_rows}'):
                lines.append(f"  exemple : {row}")

    conn.close()
    return "\n".join(lines)


if __name__ == "__main__":
    schema = get_schema()
    print(schema)
    print(f"\n{len(schema)} caractères")
