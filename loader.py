import re
import unicodedata
from pathlib import Path
from pypdf import PdfReader
from langchain_text_splitters import RecursiveCharacterTextSplitter

# Catalogue du corpus : nom du fichier dans data/ -> titre court (cité dans les réponses),
# auteurs et licence (affichés dans les sources), en-tête en 1re ligne de chaque page (header)
# et numéro de page en dernière ligne (footer)
DOCUMENTS = {
    "test.pdf": {
        "title": "Cours SQL",
        "authors": "Cours universitaire public",
        "license": "Cours public",
        "header": True,
        "footer": False,  # pied de page déjà retiré par clean_text
    },
    "database_design.pdf": {
        "title": "Database Design",
        "authors": "Adrienne Watt, Nelson Eng",
        "license": "CC BY 4.0",
        "header": False,
        "footer": True,
    },
    "postgresql.pdf": {
        "title": "PostgreSQL 18",
        "authors": "The PostgreSQL Global Development Group",
        "license": "Licence PostgreSQL",
        "header": True,
        "footer": True,
        # On ne garde que les parties sur le langage SQL : Tutorial, The SQL Language, SQL Commands
        "pages": [(40, 600), (1643, 2167)],
    },
    "redbook.pdf": {
        "title": "Red Book",
        "authors": "Peter Bailis, Joseph M. Hellerstein, Michael Stonebraker",
        "license": "CC BY-NC-ND",
        "header": True,
        "footer": True,
    },
}

# Dictionnaire : nom technique d'une lettre -> vraie lettre
SPECIAL_CHARS = {
    "eacute": "é", "egrave": "è", "ecircumflex": "ê", "agrave": "à",
    "acircumflex": "â", "ccedilla": "ç", "ugrave": "ù", "ocircumflex": "ô",
    "icircumflex": "î", "hyphen": "-", "quoteright": "'",
}


def clean_text(text):
    """Nettoie les défauts typiques des PDF générés avec LaTeX."""
    # Remplacer les ligatures Unicode : "ﬀ" -> "ff", "ﬁ" -> "fi"
    text = unicodedata.normalize("NFKC", text)

    # Réparer les lettres collées : "/f_ichiers" -> "fichiers"
    text = re.sub(r"/((?:[a-z]_)+[a-z])", lambda m: m.group(1).replace("_", ""), text)

    # Réparer les petites majuscules : "/eacute.sc" -> "é"
    def replace_small_caps(m):
        name = m.group(1)
        if len(name) == 1:
            return name
        return SPECIAL_CHARS.get(name, "")

    text = re.sub(r"/([A-Za-z]+)\.sc", replace_small_caps, text)

    # Recoller les mots coupés : "applica-\ntions" -> "applications"
    text = re.sub(r"(\w)-\n(\w)", r"\1\2", text)

    # Supprimer le pied de page "BDSQL1 2" ou "BDSQL1 VII"
    text = re.sub(r"BDSQL1\s+[0-9IVXLC]+\s*$", "", text)

    return text


def load_pdf(path, page_ranges=None):
    """Lit un PDF et renvoie une liste de pages avec leur texte nettoyé.
    page_ranges : liste de (première page, dernière page) à lire ; None = tout le document."""
    reader = PdfReader(path)
    pages = []
    for i, page in enumerate(reader.pages):
        if page_ranges and not any(a <= i + 1 <= b for a, b in page_ranges):
            continue
        text = clean_text(page.extract_text() or "")
        # Beaucoup de pointillés (". . ." ou ".....") = page de sommaire : on la saute
        if text.count(". . .") > 5 or len(re.findall(r"\.{5,}", text)) > 5:
            continue
        pages.append({"page": i + 1, "text": text})
    return pages


def strip_page_number(text):
    """Retire une dernière ligne qui n'est qu'un numéro de page ("21", "21 | Chapitre 3", "Chapitre 3 | 21")."""
    lines = text.rstrip().split("\n")
    if lines and re.fullmatch(r"\s*(\d+(\s*\|.*)?|.*\|\s*\d+)\s*", lines[-1]):
        lines = lines[:-1]
    return "\n".join(lines)


def split_pages(pages, chunk_size=1000, overlap=150):
    """Ancien découpage : chaque page en morceaux, en gardant le numéro de page."""
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=overlap,
    )
    chunks = []
    for p in pages:
        for piece in splitter.split_text(p["text"]):
            chunks.append({"page": p["page"], "text": piece})
    return chunks


def get_outline(reader):
    """Renvoie les signets du PDF : liste de (niveau, titre, page)."""
    entries = []

    def walk(items, level):
        for item in items:
            if isinstance(item, list):
                walk(item, level + 1)
            else:
                title = unicodedata.normalize("NFKC", item.title)   # espaces insécables -> espaces
                entries.append((level, title, reader.get_destination_page_number(item) + 1))

    walk(reader.outline, 0)
    return entries


def find_title(text, title, start):
    """Cherche un titre dans le texte (apostrophes et retours à la ligne tolérés)."""
    words = [re.escape(w) for w in title.replace("'", "’").split()]
    pattern = r"\s+".join(words).replace("’", "['’]")
    m = re.compile(pattern, re.IGNORECASE).search(text, start)
    return m.start() if m else -1


def split_subsections(section):
    """Recoupe une section à ses sous-titres numérotés (ex. 10.2.1, 10.2.2 dans la section 10.2).
    Renvoie une liste de (sous-titre, texte, position du texte dans la section)."""
    m = re.match(r"(\d+(?:\.\d+)*)\s", section)
    if not m:
        return [(None, section, 0)]
    child = re.compile(rf"^{re.escape(m.group(1))}\.\d+\s+\S.*$", re.MULTILINE)
    starts = [c.start() for c in child.finditer(section)]
    if not starts:
        return [(None, section, 0)]
    parts = [(None, section[:starts[0]], 0)]
    for a, b in zip(starts, starts[1:] + [len(section)]):
        part = section[a:b]
        parts.append((part.split("\n")[0].strip(), part, a))
    return parts


def split_sections(path, chunk_size=1000, overlap=150, source="Document", header=True, footer=False,
                   page_ranges=None):
    """Découpe un PDF selon ses sections, avec le titre recopié en tête de chaque chunk.
    source : titre court du document ; header / footer : retirer l'en-tête et le numéro de page ;
    page_ranges : ne garder que certaines pages (None = tout le document)."""
    reader = PdfReader(path)
    outline = get_outline(reader)
    pages = load_pdf(path, page_ranges)
    if not outline:  # PDF sans signets : on garde le découpage par page
        return [dict(c, section="", source=source) for c in split_pages(pages, chunk_size, overlap)]

    # 1. Texte complet sans les en-têtes de page, en notant où commence chaque page
    full_text = ""
    page_starts = []
    for p in pages:
        text = strip_page_number(p["text"]) if footer else p["text"]
        lines = text.split("\n")
        if header:
            lines = lines[1:]   # la 1re ligne est l'en-tête de page
        page_starts.append((len(full_text), p["page"]))
        full_text += "\n".join(lines) + "\n"

    def page_at(pos):
        """Numéro de la page qui contient la position pos."""
        return [num for start, num in page_starts if start <= pos][-1]

    def page_offset(num):
        """Position du début de la page num dans le texte complet."""
        return next((start for start, n in page_starts if n >= num), len(full_text))

    # 2. Position de chaque titre de section, grâce aux signets du PDF
    # Chemin complet de chaque signet (calculé sur tout le sommaire), puis on ne garde que les pages lues
    with_paths = []
    titles = []
    for level, title, page in outline:
        titles = titles[:level] + [title]
        with_paths.append((level, title, page, list(titles)))
    if page_ranges:
        with_paths = [e for e in with_paths if any(a <= e[2] <= b for a, b in page_ranges)]

    entries = []  # (position, niveau, chemin des titres)
    cursor = 0
    for level, title, page, titles in with_paths:
        start = max(cursor, page_offset(page))
        pos = find_title(full_text, title, start)
        pos = start if pos == -1 else full_text.rfind("\n", 0, pos) + 1
        cursor = max(pos, cursor)
        entries.append([cursor, level, list(titles)])

    # Un signet sans contenu suivi d'un signet de même niveau (ex. "Chapter 11 ..." puis le nom de l'auteur) :
    # le second garde le titre du premier, pour que les chunks portent le vrai titre du chapitre
    bounds = [e[0] for e in entries[1:]] + [len(full_text)]
    for i in range(len(entries) - 1):
        body = full_text[entries[i][0]:bounds[i]].split("\n", 1)
        empty = len(body) < 2 or len(body[1].strip()) < 30
        if empty and entries[i + 1][1] == entries[i][1]:
            entries[i + 1][2] = entries[i + 1][2][:-1] + [entries[i][2][-1]]

    markers = [(0, "Début du document")] + [(pos, " > ".join(t)) for pos, _, t in entries]
    markers.append((len(full_text), None))

    # 3. Découpage de chaque section (et sous-section), titre en tête de chaque chunk
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size, chunk_overlap=overlap, add_start_index=True
    )
    chunks = []
    for (start, title), (end, _) in zip(markers, markers[1:]):
        for subtitle, part, part_pos in split_subsections(full_text[start:end]):
            label = f"{title} > {subtitle}" if subtitle else title
            for doc in splitter.create_documents([part]):
                if len(doc.page_content.strip()) < 30:  # titre seul, sans contenu
                    continue
                pos = start + part_pos + doc.metadata["start_index"]
                chunks.append({
                    "page": page_at(pos),
                    "section": label,
                    "source": source,
                    "text": f"{source} > {label}\n{doc.page_content}",
                })
    return chunks


def load_corpus(folder="data"):
    """Découpe tous les PDF du dossier, avec les réglages du catalogue DOCUMENTS."""
    chunks = []
    for path in sorted(Path(folder).glob("*.pdf")):
        info = DOCUMENTS.get(path.name, {"title": path.stem, "header": False, "footer": True})
        doc_chunks = split_sections(
            str(path), source=info["title"], header=info["header"], footer=info["footer"],
            page_ranges=info.get("pages"),
        )
        print(f"{info['title']} : {len(doc_chunks)} chunks")
        chunks += doc_chunks
    return chunks


if __name__ == "__main__":
    chunks = load_corpus("data")
    print(f"Total : {len(chunks)} chunks")
    print("--- Exemple ---")
    print(f"(page {chunks[20]['page']})")
    print(chunks[20]["text"][:500])
