# open-steamid-locator

[![CI](https://github.com/vshxp/open-steamid-locator/actions/workflows/ci.yml/badge.svg)](https://github.com/vshxp/open-steamid-locator/actions/workflows/ci.yml)
[![Python 3.12 | 3.13](https://img.shields.io/badge/python-3.12%20%7C%203.13-3776AB?logo=python&logoColor=white)](#testes)
[![FastAPI](https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white)](#rotas)
[![Docker: Alpine](https://img.shields.io/badge/docker-alpine-2496ED?logo=docker&logoColor=white)](#imagem-de-produção)
[![Testes: 249](https://img.shields.io/badge/testes-249-brightgreen)](#testes)
[![Cobertura: 94%](https://img.shields.io/badge/cobertura-94%25-brightgreen)](#testes)
[![Vulnerabilidades: 0](https://img.shields.io/badge/vulnerabilidades-0-brightgreen?logo=docker&logoColor=white)](#zero-vulnerabilidades-e-como-se-chegou-lá)

Busca por SteamID servida por Python/FastAPI em Docker, com interface HTML+CSS em tema
escuro e interatividade via htmx (fragmentos renderizados no servidor, sem framework JS).

Digite um SteamID em qualquer formato — ou uma URL de perfil — e receba o JSON com todos
os formatos equivalentes, mais os dados de perfil quando há Steam Web API key.

Cada busca bem-sucedida é salva localmente em SQLite, com a foto do perfil em disco, sem
duplicar informação — e a base local ganha busca por nome parecido, que a Steam Web API não
oferece.

## Rodar

```sh
cp .env.example .env
docker compose up --build
```

Abra <http://localhost:8000>.

Sem `STEAM_API_KEY` a busca **já funciona**: a conversão entre formatos de SteamID é
aritmética pura e não toca a rede. Com a chave no `.env`, o resultado ganha nome, avatar,
país, data de criação e bans — e vanity URLs passam a resolver.

Chave em <https://steamcommunity.com/dev/apikey> — o passo a passo, os pré-requisitos da
conta e os limites de uso estão em [`docs/steam-api-key.md`](docs/steam-api-key.md).

## Formatos aceitos na busca

| Entrada | Exemplo | Precisa de key? |
| --- | --- | --- |
| SteamID64 | `76561197960287930` | não |
| SteamID2 | `STEAM_0:0:11101` · `STEAM_1:0:11101` | não |
| SteamID3 | `[U:1:22202]` · `U:1:22202` | não |
| Account ID | `22202` | não |
| URL de perfil | `steamcommunity.com/profiles/76561197960287930` | não |
| Vanity | `gabelogannewell` · `steamcommunity.com/id/gabelogannewell` | **sim** |

## Rotas

| Rota | Devolve |
| --- | --- |
| `GET /` | página HTML completa com o formulário de busca |
| `GET /search?q=…` | fragmento HTML com o JSON do resultado (destino do htmx) |
| `GET /perfil/{id}` | página completa e compartilhável com os dados formatados |
| `GET /salvos?q=…` | página da base local: busca ao vivo nos perfis salvos, paginada |
| `GET /salvos/lista?q=&offset=` | fragmento htmx com a lista paginada |
| `GET /avatar/{hash}` | avatar do cache local; baixa da CDN da Steam no 1º acesso |
| `GET /api/lookup?q=…` | o mesmo resultado como JSON puro |
| `GET /api/salvos?q=…` | perfis já salvos localmente; busca por nome parecido, sem tocar a Steam |
| `GET /health` | `{"status":"ok","steam_api":bool}` — usado pelo healthcheck |
| `GET /hello` | fragmento do walking skeleton, mantido como smoke test |
| `GET /docs` | OpenAPI interativa (FastAPI) |

`/search`, `/perfil/{id}` e `/api/lookup` consomem a **mesma** função (`src/lookup.py`),
então as três visões nunca divirjam entre si.

`/perfil/{id}` aceita qualquer formato aceito pela busca — `/perfil/76561197960287930`,
`/perfil/STEAM_0:0:11101` e `/perfil/gabelogannewell` levam à mesma página. O botão
**ver página** no resultado da busca sempre aponta para a forma SteamID64, que é a URL
canônica e estável.

### Forma da resposta

```json
{
  "query":   { "raw": "STEAM_0:0:11101", "detected": "steamid2" },
  "steamid": { "steamid64": "76561197960287930", "steamid2": "STEAM_1:0:11101",
               "steamid3": "[U:1:22202]", "account_id": 22202, "...": "..." },
  "steam_api": { "status": "ok | skipped | error | not_found", "...": "..." }
}
```

`steamid64` vem como **string**, não número: JavaScript perde precisão acima de 2^53 e o
SteamID64 tem 17 dígitos.

O campo `steam_api.status` é sempre explícito sobre o que aconteceu — `skipped` (sem key),
`error` (rede, 401, rate limit) ou `not_found`. A conversão é devolvida em todos os casos:
falha da Steam não derruba a resposta.

## Perfis salvos

Toda busca bem-sucedida grava o perfil em SQLite (`/app/data/perfis.sqlite3`, volume
`perfil-data`) e pré-baixa o avatar, para o perfil ficar completo em disco mesmo que ninguém
abra a página.

A interface fica em **<http://localhost:8000/salvos>** (link no pé da página inicial):
busca ao vivo com debounce de 300 ms, cards com avatar, vanity, país e situação de ban, e
paginação de 12 por página — tudo via htmx, sem JavaScript próprio. Clicar num card abre
`/perfil/{steamid64}`.

Via API:

```sh
curl -s 'http://localhost:8000/api/salvos?q=erik' | python3 -m json.tool
curl -s 'http://localhost:8000/api/salvos?limite=10&offset=0'   # q vazio: mais recentes
curl -s http://localhost:8000/health   # → "perfis": {"profiles": N, "bytes": N}
```

`offset` além do fim é **preso à última página**, então uma URL manipulada não produz
"página 4 de 3" com lista vazia.

### O dado vive uma única vez

O princípio do schema é não haver cópia de nada:

- A coluna **`raw`** guarda a resposta da Steam exatamente como veio. É a única fonte.
- Todos os campos consultáveis (`persona_name`, `country`, `vac_banned`, `created_at`, …) são
  **colunas geradas VIRTUAL** — `json_extract` computado na leitura, **zero bytes** em disco.
  Medido: uma tabela só com o JSON e outra com 8 colunas geradas ocupam exatamente os mesmos
  634.880 bytes para 2.000 linhas. E ainda assim dá para indexar: `EXPLAIN QUERY PLAN`
  confirma `SEARCH perfil USING INDEX idx_perfil_nome`.
- A busca textual usa **FTS5 com `content='perfil'`**, ou seja lê da tabela base em vez de
  guardar uma segunda cópia dos nomes. Mantida em sincronia por triggers.
- A **foto** não fica no banco. Ela já está deduplicada por construção no cache de avatares,
  endereçada pelo `avatarhash`; o banco guarda só o hash. Blob binário dentro do SQLite
  duplicaria o que o filesystem já resolve.

### Modelo temporal: só o estado atual

Cada consulta **sobrescreve** o perfil — `fetched_at` avança, `first_seen_at` é preservado.
Buscar o mesmo perfil 100 vezes deixa 1 linha, não 100. Formatos diferentes do mesmo perfil
(`STEAM_0:1:1`, `[U:1:3]`, `3`, vanity) convergem para a mesma linha, porque a chave é o
SteamID64.

> **Não há histórico.** Decisão explícita do projeto. Como a Steam Web API é
> presente-do-indicativo e não tem endpoint de histórico, mudanças de nome, aparição de ban e
> salto de playtime **não são recuperáveis depois**. Para gravá-las, o caminho é uma tabela
> `observacao(steamid64, em, hash)` referenciando estados distintos por hash de conteúdo — o
> `raw` já é serializado em forma canônica (chaves ordenadas, sem espaços) justamente para
> tornar isso barato.

### Busca por nome parecido

O FTS5 dá localmente o que a Steam Web API não oferece: **busca por nome aproximado**. A API
tem 169 métodos e nenhum busca usuário por nome — só `ResolveVanityURL`, que é exato.

O índice cobre três campos: **persona name, nome real e vanity URL**. Prefixo casa, então:

| `?q=` | acha | por quê |
| --- | --- | --- |
| `rob` | `Robin` | persona name e vanity `robinwalker` |
| `erik` | `EJ` | `real_name` "Erik Johnson" e vanity `erikj` |
| `gabe` | `Rabscuttle` | vanity `GabeLoganNewell` — o persona name não tem "gabe" |

A vanity é extraída de `profileurl` por coluna gerada: só URLs `/id/<vanity>` rendem valor,
`/profiles/<steamid64>` fica `NULL`. Como toda projeção aqui, custa zero bytes.

## Cache de avatares

O avatar é baixado **pelo servidor** no primeiro acesso, gravado em disco e servido dali em
diante. O browser só faz requisição para este servidor.

A chave de cache é o `avatarhash` da API — ele muda quando o usuário troca de avatar, então
o conteúdo de um hash é **imutável**. Isso permite servir com
`Cache-Control: public, max-age=31536000, immutable` e nunca revalidar.

```sh
curl -sD - -o /dev/null http://localhost:8000/avatar/<hash> | grep -i x-avatar-cache
```

O header `X-Avatar-Cache` diz o que aconteceu:

| Valor | Significado |
| --- | --- |
| `miss` | baixou da CDN da Steam agora e gravou em disco |
| `hit` | servido do disco, sem rede |
| `placeholder` | download falhou; devolve um SVG neutro com `no-store`, para retentar depois |

Inspecionar o tamanho do cache:

```sh
curl -s http://localhost:8000/health   # → "avatar_cache": {"files": N, "bytes": N}
```

Detalhes de implementação que importam:

- **Hash validado como 40 hex minúsculos** antes de tocar rede ou disco. O hash entra na URL
  da CDN e no nome do arquivo, então validação estrita fecha path traversal e SSRF.
- **Escrita atômica** (arquivo temporário + `rename`), para nenhuma requisição concorrente
  encontrar um JPEG truncado.
- **Magic bytes conferidos** — página de erro disfarçada de imagem não entra no cache.
- **Volume nomeado** `avatar-cache`, não bind mount: sobrevive à recriação do container e não
  deixa arquivos root-owned no repositório do host.
- O cache **cresce sem limite**. Avatares têm ~5–30 KB, então 10 mil perfis ficam na casa de
  centenas de MB. Não há política de expiração; limpar é
  `docker volume rm open-steamid-locator_avatar-cache`.

> ⚠️ `docker compose down -v` remove **os dois** volumes — apaga o cache de avatares *e o
> banco dos perfis salvos*. Para limpar só o cache, remova o volume `avatar-cache` pelo nome.

## Estrutura

```
Dockerfile             Alpine, multi-stage: build → dev (reload) / prod (não-root, sem pip)
docker-compose.yml     ambiente de desenvolvimento
requirements.txt       runtime: fastapi, uvicorn, jinja2, pydantic-settings, httpx
requirements-dev.txt   testes, lint e auditoria
pyproject.toml         configuração de pytest, coverage, ruff e bandit
.gitleaks.toml         allowlist de segredos, com justificativa por entrada
.env                   configuração única, app + Docker (fora do git)
.github/workflows/     ci.yml + reusable-{lint,test,sast,deps,secrets,container}.yml
scripts/
  smoke.sh             45 asserções contra uma instância em execução
  ci_resumo_audit.py   JSON do pip-audit → Markdown do resumo
  ci_resumo_trivy.py   JSON do trivy → Markdown, separando acionável de sem correção
tests/                 249 testes, offline por construção
src/
  main.py              rotas + lifespan do AsyncClient
  config.py            Settings lidas do .env
  steamid.py           parsing e conversão — aritmética pura, sem I/O
  steam_api.py         cliente da Steam Web API (vanity, summaries, bans)
  avatar_cache.py      download + cache em disco dos avatares
  db.py                SQLite: schema, upsert, busca FTS5
  lookup.py            orquestração: parse → converte → enriquece
  templates/           base.html, index.html, perfil.html, salvos.html, partials/*
  cache/               (volume avatar-cache) avatares baixados
  data/                (volume perfil-data) perfis.sqlite3
  static/css/          style.css
  static/js/           htmx.min.js  (2.0.4, vendorizado — sem CDN, funciona offline)
```

## Imagem de produção

```sh
docker build --target prod -t open-steamid-locator:prod .
docker run --rm -p 8000:8000 --env-file .env \
  -e DEBUG=false -v avatar-cache:/app/cache -v perfil-data:/app/data \
  open-steamid-locator:prod
```

Roda como usuário não-root (uid 1000), código embutido na imagem, sem `--reload`.

### Zero vulnerabilidades, e como se chegou lá

Medido com `trivy`, **todas as severidades**:

| base | vulnerabilidades | tamanho |
| --- | --- | --- |
| `python:3.12-slim` | **181** (4 CRITICAL, 21 HIGH) | 235 MB |
| `python:3.12-alpine` | 5 (todas no `pip`) | 131 MB |
| Alpine + venv, sem `pip`/`setuptools` | **0** | 148 MB |

As 181 vinham **todas de pacotes de sistema operacional** — `perl-base` sozinho respondia por
17 —, e **nenhuma tinha correção publicada**. Software que estava na imagem e a aplicação
nunca executa. Não se consertam: removem-se, deixando de instalar.

As 5 restantes no Alpine eram no `pip`, e essas **tinham** correção. Atualizar o pip não
resolve: ele traz `setuptools` e `msgpack`, com 2 HIGH novos. `pip` e `setuptools` são
ferramentas de *build* — a imagem final instala as dependências num venv no estágio de build
e depois remove ambos do venv e do site-packages do sistema.

Os +17 MB sobre o Alpine puro são o venv duplicado. Troca aceita: 17 MB por não ter
ferramenta de build no runtime.

O `/venv` fica de root e é apenas lido pelo processo. `chown -R` nele reescreveria cada
arquivo numa camada nova — custava +55 MB de imagem sem ganho algum.

### Riscos do musl, verificados

Alpine usa musl em vez de glibc. O que foi testado antes de adotar:

| risco | resultado |
| --- | --- |
| dependência sem wheel musl, forçando compilação | nenhuma compila; `uvloop`, `httptools`, `watchfiles` e `pydantic-core` têm wheel |
| resolução DNS do musl | resolve `api.steampowered.com` e `avatars.steamstatic.com` igual ao glibc |
| TLS de saída | HTTP 200 real da Steam Web API; avatar baixado da CDN e validado |
| desempenho (malloc do musl) | 1287 ms vs 1245 ms em 200 requisições — dentro do ruído |
| suíte de testes | 249/249 sobre musl, nas duas versões do Python |
| hot-reload no alvo `dev` | `watchfiles` reage a edição de template e de código |

> ⚠️ **`DEBUG=false` em produção não é opcional.** Com `debug=true`, o Starlette devolve a
> página de traceback em erro não tratado, expondo trechos de código e valores de variáveis
> locais. O `.env.example` traz `DEBUG=true` porque é o padrão de desenvolvimento — sobrescreva
> ao publicar.

> A imagem `dev` **não** contém `src/` — o código chega por volume no compose. Rodar
> `docker run` na imagem `dev` sem montar `./src` falha; use o alvo `prod`.

## Testes

249 testes, 94% de cobertura. A suíte é **offline por construção**: todo cliente HTTP usa
`httpx.MockTransport` e os diretórios de dados apontam para temporários. Não precisa de
`STEAM_API_KEY`, nem de rede, nem de Docker.

```sh
docker compose exec app pytest              # dentro do container de dev
docker compose exec app pytest --cov=src    # com cobertura
```

Fora do Docker:

```sh
pip install -r requirements-dev.txt
pytest
```

| arquivo | cobre |
| --- | --- |
| `test_steamid.py` | parsing e conversão: convergência de formatos, round-trip, entradas inválidas |
| `test_db.py` | schema sem duplicação, upsert, FTS, clamp de offset, migração |
| `test_avatar_cache.py` | validação de hash, download, escrita atômica, magic bytes |
| `test_steam_api.py` | mapeamento de erros HTTP e proteção da API key |
| `test_lookup.py` | orquestração, os quatro `status`, aviso de privacidade |
| `test_routes.py` | rotas, fragmentos htmx, XSS, traversal, paginação |
| `test_filters.py` | filtros de formatação dos templates |

Vários testes são de **regressão** para bugs que de fato ocorreram, e estão marcados com o
motivo no código — para ninguém "simplificar" e reintroduzi-los. Os três principais:

- `PRAGMA table_info` **omite colunas geradas**; usá-lo na migração fazia a aplicação não
  subir com `duplicate column name`.
- Aspa no termo de busca escapava do aspeamento e virava sintaxe FTS5 → HTTP 500.
- `offset` além do fim exibia "página 4 de 3" com lista vazia.

### Smoke / DAST

`scripts/smoke.sh` faz 45 asserções contra uma instância **em execução** — container real,
uvicorn real, rede real. Pega o que teste de unidade não pega: imagem sem arquivo, permissão
errada em volume, rota que só quebra fora do `TestClient`.

```sh
docker compose up -d
scripts/smoke.sh http://localhost:8000
```

## CI

`.github/workflows/ci.yml` é só orquestração; cada validação vive num workflow reutilizável
(`reusable-*.yml`), chamável isoladamente e com entradas próprias.

| etapa | valida | ferramenta |
| --- | --- | --- |
| `lint` | estilo, imports, bugbear, regras de segurança; e os próprios workflows | ruff, actionlint |
| `test` | comportamento e cobertura, em Python 3.12 e 3.13 | pytest, coverage |
| `sast` | padrões inseguros em `src/` | bandit |
| `deps` | CVE em dependências de runtime e de dev | pip-audit |
| `secrets` | segredo em **todo o histórico**, não só no diff | gitleaks |
| `container` | Dockerfile, CVE da imagem, misconfiguração de IaC, DAST | hadolint, trivy, `smoke.sh` |
| `sumario` | consolida tudo numa tabela no resumo da execução | — |

O pipeline **nunca fala com a Steam**: a suíte é offline e o smoke roda no modo sem chave.
Não há segredo configurado no repositório.

Roda em push para `main`, em pull request, sob demanda, e **semanalmente** — CVE nova aparece
sem ninguém commitar nada, e um pipeline que só dispara em push descobre isso tarde.

### Duas decisões que valem saber

**A imagem tem zero vulnerabilidades, em todas as severidades.** Não por sorte: a base é
Alpine e a imagem final não carrega `pip` nem `setuptools`. Ver a seção da imagem abaixo.

**O portão vai até MEDIUM.** As únicas CVEs acionáveis que a imagem já teve estavam nessa
faixa — no próprio `pip` —, e um portão restrito a HIGH/CRITICAL as deixava passar.

**CVE sem correção não bloqueia.** O portão usa `--ignore-unfixed`: gasta seu crédito no que
dá para consertar. Hoje não há nenhuma na imagem, mas a regra continua valendo: se o upstream
publicar uma CVE sem correção disponível, ela aparece no resumo e não trava o pipeline. Um
pipeline cronicamente vermelho deixa de ser sinal.

**Varredura inconclusiva falha fechada.** Se o pip-audit ou o trivy morrem antes de escrever
o relatório, o resumo reporta `inconclusivo` e o portão falha. Auditoria que quebrou no meio
não pode passar por sinal verde.

### Reproduzir localmente

Todas as ferramentas rodam via imagem Docker pinada, então o que roda no CI é exatamente o
que roda aqui:

```sh
docker run --rm -i hadolint/hadolint:2.12.0 hadolint - < Dockerfile
docker run --rm -v "$PWD:/repo" -w /repo rhysd/actionlint:1.7.7
docker run --rm -v "$PWD:/repo" aquasec/trivy:0.58.1 config /repo
docker run --rm -v "$PWD:/repo" -w /repo zricethezav/gitleaks:v8.21.2 \
  detect --source /repo --config /repo/.gitleaks.toml --redact
```

> `gitleaks detect --no-git` acusa a chave real no `.env` em disco. Isso é **correto**:
> `.env` é deliberadamente ausente da allowlist, para que um commit acidental dele seja
> detectado. O CI varre o histórico git, onde o `.env` nunca existiu.

## Segurança

O que já está tratado no código:

| Vetor | Tratamento |
| --- | --- |
| Vazamento da API key | A chave nunca entra em resposta HTTP nem em log. `/health` expõe só um booleano. Erros de rede reportam o **nome da classe** da exceção, nunca seu texto — a URL da Steam carrega a chave e algumas exceções do httpx incluem a URL. Os loggers `httpx`/`httpcore` são silenciados em `WARNING` para a URL não cair no log. |
| Path traversal em `/avatar/{hash}` | Hash validado como **40 hex minúsculos** antes de tocar disco ou rede. |
| SSRF em `/avatar/{hash}` | A URL da CDN é montada pelo servidor; do usuário vem apenas o hash já validado. O host nunca é controlável. |
| XSS refletido | Autoescape do Jinja em todos os templates; a entrada crua do usuário é ecoada escapada nas mensagens de erro. |
| XSS via esquema de URI | `profileurl` vem da Steam e vai para um `href`: só passa com esquema `http`/`https`, o que bloqueia `javascript:` e `data:`. |
| Cache envenenado | Magic bytes de JPEG conferidos e tamanho limitado a 2 MB antes de gravar. Escrita atômica, sem arquivo truncado servido. |
| Segredos no repositório | `.env` no `.gitignore` e no `.dockerignore`; não entra em nenhuma camada da imagem. |
| CSRF | Não há sessão, cookie ou autenticação, e todos os endpoints são `GET` somente-leitura. Sem superfície. |
| SQL injection | Toda consulta usa parâmetros vinculados; nenhum valor de usuário é concatenado em SQL. |
| Injeção na sintaxe do FTS5 | O termo de busca nunca vira sintaxe: cada palavra é aspeada e as aspas do texto são dobradas (`""` é aspa literal no FTS5). Sem isso, um `"` no termo fecharia a string e o resto seria lido como operador. Há ainda um `except OperationalError` que devolve zero resultados em vez de 500. |

### Antes de expor isto na internet

O projeto foi construído para rodar local. Dois pontos precisam de trabalho antes de um
deploy público:

1. **Não há rate limiting nas rotas próprias.** Cada busca gasta 2 chamadas da Steam Web API.
   Quem alcançar `/api/lookup` em laço queima sua cota de 100.000 chamadas/dia e pode te levar
   ao throttling descrito em [`docs/steam-api-key.md`](docs/steam-api-key.md). Ponha limite por
   IP na frente, ou cache de resultados.
2. **`DEBUG=false`**, conforme o aviso acima.

Também vale saber: a `/docs` (OpenAPI interativa) fica aberta por padrão. Ela não expõe
segredo algum, mas revela a superfície da API — desative se isso incomodar.

## Notas

- O htmx está **vendorizado** em `src/static/js/htmx.min.js`. Nada é buscado de CDN, então
  a página funciona sem internet. Ao atualizar, troque o arquivo e o comentário de versão
  em `src/templates/base.html`.
- **O browser nunca fala com domínio externo.** Avatares são baixados pelo servidor e
  servidos de `/avatar/{hash}` — ver seção abaixo. Os únicos endereços externos nas páginas
  são `href` de links que o usuário clica.
- O container sempre escuta na porta 8000 internamente; `PORT` no `.env` controla apenas o
  mapeamento no host.
- Perfil privado na Steam devolve HTTP 200 com campos ausentes, não erro. O bloco
  `interpreted` marca isso com um `aviso` explícito para "não pude ver" não ser confundido
  com "não existe".
