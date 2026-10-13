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


if __name__ == "__main__":
    pages = load_pdf("data/test.pdf")
    chunks = split_pages(pages)
    print(f"{len(pages)} pages -> {len(chunks)} chunks")
    print("--- Exemple : chunk n°20 ---")
    print(f"(page {chunks[20]['page']})")
    print(f"Longueur : {len(chunks[20]['text'])} caractères")