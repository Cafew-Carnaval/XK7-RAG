# Image de XK7 : Python 3.12 + dépendances + code + données. Une seule image, deux usages :
#   l'application Streamlit (port 8501) et l'API FastAPI (port 8000), lancées par docker-compose.yml.
FROM python:3.12-slim

# Pas de fichiers .pyc, journaux affichés tout de suite
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

WORKDIR /app

# Les dépendances d'abord : cette couche reste en cache tant que requirements.txt ne change pas,
# donc modifier le code ne réinstalle pas tout
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Puis le code, les données de démonstration et la base vectorielle (voir .dockerignore pour ce qui est exclu)
COPY . .

# Les secrets (OPENAI_API_KEY, APP_PASSWORD, XK7_API_KEY) ne sont PAS dans l'image :
# ils sont donnés au lancement (fichier .env lu par docker-compose)
EXPOSE 8501 8000

# Par défaut : l'application. docker-compose.yml lance aussi l'API avec une autre commande.
CMD ["streamlit", "run", "app.py", "--server.address=0.0.0.0", "--server.port=8501", "--server.headless=true"]
