# syntax=docker/dockerfile:1

# ---- base: dependências comuns aos dois alvos ----
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ---- dev: código vem por volume, uvicorn com --reload ----
FROM base AS dev

ENV DEBUG=true

EXPOSE 8000
CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload", "--reload-dir", "/app/src"]

# ---- prod: código embutido na imagem, usuário não-root, sem reload ----
FROM base AS prod

COPY src ./src

# O diretório precisa existir na imagem e já pertencer ao app: um volume nomeado
# montado em /app/cache herda dono e permissão do que estava ali na imagem. Sem
# isto o Docker cria o ponto de montagem como root e o processo não escreve.
RUN mkdir -p /app/cache/avatars \
    && useradd --create-home --uid 1000 app \
    && chown -R app:app /app
USER app

EXPOSE 8000
CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]
