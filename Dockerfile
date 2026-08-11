# syntax=docker/dockerfile:1
#
# Base Alpine em vez de Debian slim, e nenhuma ferramenta de build na imagem final.
# Medido com trivy, todas as severidades:
#
#   python:3.12-slim                     181 vulnerabilidades   235 MB
#   python:3.12-alpine                     5 (todas no pip)     131 MB
#   este Dockerfile                        0                    148 MB
#
# As 181 vinham de pacotes de SO — perl-base, util-linux, libc6, ncurses, tar —
# que a aplicação nunca executa, e nenhuma tinha correção publicada. Não se
# consertam: removem-se, deixando de instalar o software.
#
# Os +17 MB sobre o Alpine puro são o venv duplicado. Aceito: troca 17 MB por não
# ter pip nem setuptools no runtime, que é onde estavam as únicas CVEs acionáveis.

# ---- build: dependências de runtime num venv ----
FROM python:3.12-alpine AS build

ENV PIP_NO_CACHE_DIR=1

# WORKDIR explícito para o destino relativo do COPY não ser ambíguo. Nada daqui
# vai para a imagem final além do /venv.
WORKDIR /build

RUN python -m venv /venv
COPY requirements.txt .
RUN /venv/bin/pip install --no-cache-dir -r requirements.txt

# ---- build-dev: acrescenta ao mesmo venv as ferramentas de teste e auditoria ----
FROM build AS build-dev

COPY requirements-dev.txt .
RUN /venv/bin/pip install --no-cache-dir -r requirements-dev.txt

# ---- runtime: o que dev e prod têm em comum ----
FROM python:3.12-alpine AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/venv/bin:$PATH"

WORKDIR /app

# Nenhum pacote apk é instalado: o healthcheck usa a stdlib do Python. Evita uma
# camada de gerenciador de pacotes, reduz a superfície do scanner e dispensa pinar
# versão de pacote de sistema — que quebra o build quando a versão sai do mirror.
#
# Definido aqui, não no compose: assim `docker run` puro também tem healthcheck.
# urlopen levanta em status != 2xx e em falha de conexão, então o código de saída
# já reflete a saúde sem verificação explícita.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request as u; u.urlopen('http://127.0.0.1:8000/health', timeout=3)"]

EXPOSE 8000

# ---- dev: código vem por volume, uvicorn com --reload ----
FROM runtime AS dev

# Mantém pip e as ferramentas de desenvolvimento: esta imagem não é publicada, e
# o pip-audit precisa importar o pip para funcionar.
COPY --from=build-dev /venv /venv

ENV DEBUG=true

CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload", "--reload-dir", "/app/src"]

# ---- prod: código embutido, usuário não-root, sem reload, sem pip ----
FROM runtime AS prod

COPY --from=build /venv /venv

# pip e setuptools são ferramentas de build; num runtime só ampliam superfície.
# Removidos do venv e do site-packages do sistema — a imagem base do Python traz
# a sua própria cópia, que o scanner também enxerga.
RUN /venv/bin/python -m pip uninstall -y pip setuptools wheel 2>/dev/null || true; \
    python -m pip uninstall -y pip setuptools wheel 2>/dev/null || true; \
    rm -rf /usr/local/lib/python3.12/site-packages/pip* \
           /usr/local/lib/python3.12/site-packages/setuptools* \
           /usr/local/lib/python3.12/site-packages/pkg_resources \
           /usr/local/lib/python3.12/ensurepip \
           /venv/lib/python3.12/site-packages/pip* \
           /venv/lib/python3.12/site-packages/setuptools* \
           /usr/local/bin/pip*

COPY src ./src

# Só /app é dado a `app`. O /venv fica de root e é apenas lido — `chown -R` nele
# reescreveria cada arquivo numa camada nova, duplicando o venv inteiro na imagem
# (custava +55 MB).
#
# Os diretórios de cache e dados precisam existir na imagem e já pertencer ao app:
# um volume nomeado montado em /app/cache herda dono e permissão do que estava ali.
# Sem isto o Docker cria o ponto de montagem como root e o processo não escreve.
RUN mkdir -p /app/cache/avatars /app/data \
    && adduser -D -u 1000 app \
    && chown -R app:app /app
USER app

CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]
