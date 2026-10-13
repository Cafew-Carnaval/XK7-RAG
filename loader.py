import re
import unicodedata
from pypdf import PdfReader
from langchain_text_splitters import RecursiveCharacterTextSplitter

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

    # Supprimer le pied de page "BDSQL1 2"
    text = re.sub(r"BDSQL1\s+\d+\s*$", "", text)

    return text


def load_pdf(path):
    """Lit un PDF et renvoie une liste de pages avec leur texte nettoyé."""
    reader = PdfReader(path)
    pages = []
    for i, page in enumerate(reader.pages):
        text = clean_text(page.extract_text() or "")
        if text.count(". . .") > 5:   # beaucoup de pointillés = page de sommaire
            continue                  # on saute cette page
        pages.append({"page": i + 1, "text": text})
    return pages


def split_pages(pages, chunk_size=1000, overlap=150):
    """Découpe chaque page en morceaux (chunks), en gardant le numéro de page."""
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
                entries.append((level, item.title, reader.get_destination_page_number(item) + 1))

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


def split_sections(path, chunk_size=1000, overlap=150):
    """Découpe le PDF selon ses sections, avec le titre recopié en tête de chaque chunk."""
    reader = PdfReader(path)
    outline = get_outline(reader)
    pages = load_pdf(path)
    if not outline:  # PDF sans signets : on garde le découpage par page
        return split_pages(pages, chunk_size, overlap)

    # 1. Texte complet sans les en-têtes de page, en notant où commence chaque page
    full_text = ""
    page_starts = []
    for p in pages:
        lines = p["text"].split("\n")
        page_starts.append((len(full_text), p["page"]))
        full_text += "\n".join(lines[1:]) + "\n"   # la 1re ligne est l'en-tête de page

    def page_at(pos):
        """Numéro de la page qui contient la position pos."""
        return [num for start, num in page_starts if start <= pos][-1]

    def page_offset(num):
        """Position du début de la page num dans le texte complet."""
        return next((start for start, n in page_starts if n >= num), len(full_text))

    # 2. Position de chaque titre de section, grâce aux signets du PDF
    markers = [(0, "Début du document")]
    titles = []
    cursor = 0
    for level, title, page in outline:
        titles = titles[:level] + [title]
        start = max(cursor, page_offset(page))
        pos = find_title(full_text, title, start)
        pos = start if pos == -1 else full_text.rfind("\n", 0, pos) + 1
        cursor = max(pos, cursor)
        markers.append((cursor, " > ".join(titles)))
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
                    "text": f"Section : {label}\n{doc.page_content}",
                })
    return chunks

if __name__ == "__main__":
    chunks = split_sections("data/test.pdf")
    print(f"{len(chunks)} chunks")
    print("--- Exemple : chunk n°20 ---")
    print(f"(page {chunks[20]['page']})")
    print(chunks[20]["text"])