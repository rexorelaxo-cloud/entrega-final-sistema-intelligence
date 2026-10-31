FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /srv/app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app ./app
COPY scripts ./scripts
COPY knowledge ./knowledge

RUN useradd --create-home appuser && mkdir -p /data/chroma && chown -R appuser /data /srv/app
USER appuser

EXPOSE 8000
CMD ["uvicorn", "app.main:app_produccion", "--factory", "--host", "0.0.0.0", "--port", "8000"]
