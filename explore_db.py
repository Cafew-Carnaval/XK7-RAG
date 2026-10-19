import sqlite3

DB_PATH = "data/chinook.db"


def explore(db_path=DB_PATH, sample_rows=3):
    """Affiche les tables de la base, leurs colonnes, leurs liens et quelques lignes."""
    # mode=ro : la base est ouverte en lecture seule, impossible de la modifier par erreur
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    cur = conn.cursor()

    tables = [row[0] for row in cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    )]
    print(f"{len(tables)} tables : {', '.join(tables)}\n")

    for table in tables:
        count = cur.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
        print(f"=== {table} ({count} lignes)")

        # Colonnes : nom, type, clé primaire
        for col in cur.execute(f'PRAGMA table_info("{table}")'):
            _, name, col_type, _, _, pk = col
            print(f"  {name:<20}{col_type:<16}{'clé primaire' if pk else ''}")

        # Clés étrangères : les liens vers les autres tables
        for fk in cur.execute(f'PRAGMA foreign_key_list("{table}")'):
            print(f"  lien : {fk[3]} -> {fk[2]}.{fk[4]}")

        # Quelques lignes pour voir à quoi ressemblent les données
        cur.execute(f'SELECT * FROM "{table}" LIMIT {sample_rows}')
        for row in cur.fetchall():
            print("   ", row)
        print()

    conn.close()


if __name__ == "__main__":
    explore()
