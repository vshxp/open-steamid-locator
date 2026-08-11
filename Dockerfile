# syntax=docker/dockerfile:1

# ---- base: dependências comuns aos dois alvos ----
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Nenhum pacote apt é instalado de propósito. O healthcheck usa a stdlib do
# Python (ver docker-compose.yml), o que evita uma camada inteira do apt: imagem
# menor, menos pacotes para o scanner de CVE e sem o dilema do DL3008 — pinar
# versão de apt quebra o build quando a versão sai do mirror, e não pinar é
# achado de lint.

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Definido na imagem, não no compose: assim `docker run` puro também tem
# healthcheck. urlopen levanta em status != 2xx e em falha de conexão, então o
# código de saída já reflete a saúde sem verificação explícita.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request as u; u.urlopen('http://127.0.0.1:8000/health', timeout=3)"]

# ---- dev: código vem por volume, uvicorn com --reload ----
FROM base AS dev

# Ferramentas de teste, lint e auditoria só neste alvo — a imagem de produção
# não as carrega.
COPY requirements-dev.txt .
RUN pip install --no-cache-dir -r requirements-dev.txt

ENV DEBUG=true

EXPOSE 8000
CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload", "--reload-dir", "/app/src"]

# ---- prod: código embutido na imagem, usuário não-root, sem reload ----
FROM base AS prod

COPY src ./src

# O diretório precisa existir na imagem e já pertencer ao app: um volume nomeado
# montado em /app/cache herda dono e permissão do que estava ali na imagem. Sem
# isto o Docker cria o ponto de montagem como root e o processo não escreve.
RUN mkdir -p /app/cache/avatars /app/data \
    && useradd --create-home --uid 1000 app \
    && chown -R app:app /app
USER app

EXPOSE 8000
CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]
