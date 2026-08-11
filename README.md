# open-steamid-locator

Busca por SteamID servida por Python/FastAPI em Docker, com interface HTML+CSS em tema
escuro e interatividade via htmx (fragmentos renderizados no servidor, sem framework JS).

Digite um SteamID em qualquer formato — ou uma URL de perfil — e receba o JSON com todos
os formatos equivalentes, mais os dados de perfil quando há Steam Web API key.

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
| `GET /avatar/{hash}` | avatar do cache local; baixa da CDN da Steam no 1º acesso |
| `GET /api/lookup?q=…` | o mesmo resultado como JSON puro |
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
  centenas de MB. Não há política de expiração; limpar é `docker compose down -v` ou
  `docker volume rm open-steamid-locator_avatar-cache`.

## Estrutura

```
Dockerfile             multi-stage: base → dev (reload) / prod (não-root, sem reload)
docker-compose.yml     ambiente de desenvolvimento
requirements.txt       fastapi, uvicorn, jinja2, pydantic-settings, httpx
.env                   configuração única, app + Docker (fora do git)
src/
  main.py              rotas + lifespan do AsyncClient
  config.py            Settings lidas do .env
  steamid.py           parsing e conversão — aritmética pura, sem I/O
  steam_api.py         cliente da Steam Web API (vanity, summaries, bans)
  avatar_cache.py      download + cache em disco dos avatares
  lookup.py            orquestração: parse → converte → enriquece
  templates/           base.html, index.html, perfil.html, partials/*
  cache/               (volume) avatares baixados — fora de src/ e do git
  static/css/          style.css
  static/js/           htmx.min.js  (2.0.4, vendorizado — sem CDN, funciona offline)
```

## Imagem de produção

```sh
docker build --target prod -t open-steamid-locator:prod .
docker run --rm -p 8000:8000 --env-file .env \
  -e DEBUG=false -v avatar-cache:/app/cache \
  open-steamid-locator:prod
```

Roda como usuário não-root, código embutido na imagem, sem `--reload`.

> ⚠️ **`DEBUG=false` em produção não é opcional.** Com `debug=true`, o Starlette devolve a
> página de traceback em erro não tratado, expondo trechos de código e valores de variáveis
> locais. O `.env.example` traz `DEBUG=true` porque é o padrão de desenvolvimento — sobrescreva
> ao publicar.

> A imagem `dev` **não** contém `src/` — o código chega por volume no compose. Rodar
> `docker run` na imagem `dev` sem montar `./src` falha; use o alvo `prod`.

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
- Ainda **não** há persistência. A resposta é sempre uma consulta ao vivo; nada é gravado.
