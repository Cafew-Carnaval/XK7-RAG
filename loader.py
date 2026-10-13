import re
from pypdf import PdfReader
import unicodedata
from pypdf import PdfReader

# Dictionnaire : nom technique d'une lettre -> vraie lettre
SPECIAL_CHARS = {
    "eacute": "é", "egrave": "è", "ecircumflex": "ê", "agrave": "à",
    "acircumflex": "â", "ccedilla": "ç", "ugrave": "ù", "ocircumflex": "ô",
    "icircumflex": "î", "hyphen": "-", "quoteright": "'",
}


# ÉTAPE 3 : nettoyer le texte
def clean_text(text):
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

    return text


# ÉTAPES 1 et 2 : ouvrir le PDF et lire chaque page
def load_pdf(path):
    reader = PdfReader(path)
    pages = []
    for i, page in enumerate(reader.pages):
        text = page.extract_text() or ""
        pages.append({"page": i + 1, "text": clean_text(text)})
    return pages


# TEST : ne se lance que si on tape "python loader.py"
if __name__ == "__main__":
    pages = load_pdf("data/test.pdf")
    print(f"{len(pages)} pages lues")
    print("--- Fin de la page 10 ---")
    print(pages[9]["text"][-600:])