# Моносервис: FastAPI отдаёт /api и собранный фронт (frontend/dist коммитится в репозиторий).
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PORT=8000

WORKDIR /srv

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .
# var/ — утверждённые заказы и кэш; единственное место, куда сервис пишет
RUN useradd --system --uid 1001 app && mkdir -p var && chown app var
USER app

EXPOSE 8000
CMD ["python", "-m", "app"]
