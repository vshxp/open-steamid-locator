import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import httpx
from fastapi import Depends, FastAPI, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from src.avatar_cache import AVATAR_HASH_RE, AvatarCache, AvatarCacheError
from src.config import BASE_DIR, settings
from src.db import ProfileStore, documento_canonico
from src.lookup import lookup
from src.steam_api import SteamClient
from src.steamid import SteamIdError

log = logging.getLogger("open_steamid_locator")


# O httpx loga "HTTP Request: GET <url>" em nível INFO. A URL da Steam Web API
# carrega a API key na query string, então esse log gravaria a chave em texto
# puro nos logs do container — que costumam ser colados em issues e prints.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Um único AsyncClient para todo o processo — conexões reaproveitadas.

    O cliente é criado sempre, mesmo sem STEAM_API_KEY: o cache de avatares
    baixa da CDN pública da Steam, que não pede chave. Já o SteamClient só
    existe quando há chave — sem ela a aplicação ainda converte SteamID, que é
    aritmética pura.
    """
    http = httpx.AsyncClient(
        timeout=httpx.Timeout(10.0),
        headers={"User-Agent": f"{settings.app_name}/0.1"},
    )
    app.state.http = http
    app.state.steam = (
        SteamClient(settings.steam_api_key, http) if settings.steam_api_key else None
    )
    app.state.avatars = AvatarCache(settings.cache_dir / "avatars", http)
    app.state.store = ProfileStore(settings.data_dir / "perfis.sqlite3")

    try:
        yield
    finally:
        await http.aclose()


app = FastAPI(title=settings.app_name, debug=settings.debug, lifespan=lifespan)

app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")

templates = Jinja2Templates(directory=BASE_DIR / "templates")


def _fmt_datahora(iso: str | None) -> str:
    """ISO 8601 → '12/09/2003 às 11:14 UTC'."""
    if not iso:
        return "—"
    try:
        return datetime.fromisoformat(iso).strftime("%d/%m/%Y às %H:%M UTC")
    except ValueError:
        return iso


def _fmt_idade(iso: str | None) -> str:
    """ISO 8601 → 'há 22 anos e 11 meses'. Aproximado — serve para leitura humana."""
    if not iso:
        return ""
    try:
        criada = datetime.fromisoformat(iso)
    except ValueError:
        return ""
    dias = (datetime.now(timezone.utc) - criada).days
    if dias < 0:
        return ""
    anos, resto = divmod(dias, 365)
    meses = resto // 30
    partes = []
    if anos:
        partes.append(f"{anos} ano{'s' if anos != 1 else ''}")
    if meses:
        partes.append(f"{meses} {'meses' if meses != 1 else 'mês'}")
    if not partes:
        return f"há {dias} dia{'s' if dias != 1 else ''}"
    return "há " + " e ".join(partes)


def _fmt_bandeira(codigo: str | None) -> str:
    """Código ISO 3166-1 alpha-2 → emoji de bandeira, via regional indicators."""
    if not codigo or len(codigo) != 2 or not codigo.isalpha():
        return ""
    return "".join(chr(0x1F1E6 + ord(c) - ord("A")) for c in codigo.upper())


def _fmt_ano(timestamp: int | None) -> str:
    """Timestamp Unix → apenas o ano. Para a listagem compacta dos cards."""
    if not timestamp:
        return ""
    try:
        return str(datetime.fromtimestamp(timestamp, tz=timezone.utc).year)
    except (ValueError, OSError, OverflowError):
        return ""


templates.env.filters["ano"] = _fmt_ano
templates.env.filters["datahora"] = _fmt_datahora
templates.env.filters["idade"] = _fmt_idade
templates.env.filters["bandeira"] = _fmt_bandeira


def get_steam_client(request: Request) -> SteamClient | None:
    return request.app.state.steam


def get_avatar_cache(request: Request) -> AvatarCache:
    return request.app.state.avatars


def get_store(request: Request) -> ProfileStore:
    return request.app.state.store


async def persistir(result: dict, store: ProfileStore, cache: AvatarCache) -> dict:
    """Salva o perfil e garante a foto em disco. Devolve `result` enriquecido.

    Só grava quando a Steam devolveu dados: sem chave ou com erro de API não há
    perfil para salvar, e gravar um documento vazio apagaria o que já estava lá.

    Falha de persistência nunca derruba a resposta — a busca continua útil mesmo
    que o disco esteja cheio ou o banco travado.
    """
    api = result.get("steam_api", {})
    if api.get("status") != "ok":
        return result

    cru = api.get("raw") or {}
    summary, bans = cru.get("summary"), cru.get("bans")
    if summary is None and bans is None:
        return result

    steamid64 = result["steamid"]["steamid64"]
    agora = int(datetime.now(timezone.utc).timestamp())

    try:
        meta = await store.save(steamid64, documento_canonico(summary, bans), agora)
        result["stored"] = meta
    except Exception:
        log.exception("falha ao salvar perfil %s", steamid64)
        result["stored"] = {"erro": "não foi possível salvar o perfil"}
        return result

    # Pré-baixa a foto para o perfil estar completo em disco mesmo que ninguém
    # abra a página. Idempotente: mesmo hash, mesmo arquivo, baixado uma vez.
    avatar_hash = (api.get("interpreted") or {}).get("avatar_hash")
    if avatar_hash and AVATAR_HASH_RE.match(avatar_hash) and not cache.cached(avatar_hash):
        try:
            await cache.fetch(avatar_hash)
        except AvatarCacheError as exc:
            log.warning("avatar %s não baixado: %s", avatar_hash, exc)

    return result


# Placeholder servido quando o avatar não pôde ser obtido. Fica inline em SVG
# para a página nunca precisar de um recurso externo, nem mesmo no caminho de
# falha.
_PLACEHOLDER_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="184" height="184" '
    'viewBox="0 0 184 184" role="img" aria-label="Avatar indisponível">'
    '<rect width="184" height="184" fill="#10131a"/>'
    '<circle cx="92" cy="72" r="30" fill="#2a2f3a"/>'
    '<path d="M32 184c0-33 27-60 60-60s60 27 60 60z" fill="#2a2f3a"/>'
    "</svg>"
)


def _page_context() -> dict:
    return {
        "app_name": settings.app_name,
        "steam_api_enabled": settings.steam_api_key != "",
    }


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """Página completa: formulário de busca em tema escuro."""
    return templates.TemplateResponse(request, "index.html", _page_context())


@app.get("/search", response_class=HTMLResponse)
async def search(
    request: Request,
    q: str = Query("", description="SteamID64, STEAM_0:1:x, [U:1:x], URL de perfil ou vanity"),
    client: SteamClient | None = Depends(get_steam_client),
    store: ProfileStore = Depends(get_store),
    cache: AvatarCache = Depends(get_avatar_cache),
):
    """Fragmento htmx: o JSON do resultado, ou a mensagem de erro.

    Devolve 200 mesmo em erro de entrada — htmx não faz swap em 4xx por padrão,
    e aqui o erro é conteúdo a exibir, não falha de transporte.
    """
    try:
        result = await persistir(await lookup(q, client), store, cache)
    except SteamIdError as exc:
        return templates.TemplateResponse(
            request, "partials/error.html", {"message": str(exc)}
        )

    return templates.TemplateResponse(
        request,
        "partials/result.html",
        {
            "result": result,
            "json_text": json.dumps(result, indent=2, ensure_ascii=False),
        },
    )


@app.get("/avatar/{avatar_hash}")
async def avatar(
    avatar_hash: str,
    cache: AvatarCache = Depends(get_avatar_cache),
):
    """Serve o avatar do cache local, baixando da CDN da Steam no primeiro acesso.

    O header `X-Avatar-Cache` diz o que aconteceu: `hit`, `miss` ou `placeholder`.
    """
    if not AVATAR_HASH_RE.match(avatar_hash):
        return JSONResponse(
            status_code=400,
            content={"error": "avatarhash inválido — esperado 40 caracteres hex."},
        )

    origem = "hit"
    caminho = cache.cached(avatar_hash)

    if caminho is None:
        origem = "miss"
        try:
            caminho = await cache.fetch(avatar_hash)
        except AvatarCacheError:
            # Sem `no-store` um blip de rede congelaria o placeholder no browser.
            return Response(
                content=_PLACEHOLDER_SVG,
                media_type="image/svg+xml",
                headers={"Cache-Control": "no-store", "X-Avatar-Cache": "placeholder"},
            )

    return FileResponse(
        caminho,
        media_type="image/jpeg",
        headers={
            # O hash muda quando o avatar muda, então o conteúdo é imutável.
            "Cache-Control": "public, max-age=31536000, immutable",
            "X-Avatar-Cache": origem,
        },
    )


@app.get("/perfil/{query}", response_class=HTMLResponse)
async def perfil(
    request: Request,
    query: str,
    client: SteamClient | None = Depends(get_steam_client),
    store: ProfileStore = Depends(get_store),
    cache: AvatarCache = Depends(get_avatar_cache),
):
    """Página completa e compartilhável com os dados da API já formatados.

    Aceita qualquer formato que a busca aceita, então a URL pode ser
    /perfil/76561197960287930 ou /perfil/gabelogannewell.
    """
    try:
        result = await persistir(await lookup(query, client), store, cache)
    except SteamIdError as exc:
        return templates.TemplateResponse(
            request,
            "perfil_erro.html",
            {**_page_context(), "message": str(exc), "query": query},
            status_code=404,
        )

    return templates.TemplateResponse(
        request,
        "perfil.html",
        {
            **_page_context(),
            "result": result,
            "json_text": json.dumps(result, indent=2, ensure_ascii=False),
        },
    )


@app.get("/api/lookup")
async def api_lookup(
    q: str = Query("", description="SteamID64, STEAM_0:1:x, [U:1:x], URL de perfil ou vanity"),
    client: SteamClient | None = Depends(get_steam_client),
    store: ProfileStore = Depends(get_store),
    cache: AvatarCache = Depends(get_avatar_cache),
):
    """Mesma busca, como JSON puro — para consumo programático."""
    try:
        return await persistir(await lookup(q, client), store, cache)
    except SteamIdError as exc:
        return JSONResponse(status_code=400, content={"error": str(exc), "query": q})


PAGINA_SALVOS = 12


def _contexto_lista(dados: dict, q: str, limite: int) -> dict:
    """Monta o contexto de paginação.

    Usa o offset **efetivo** devolvido pela consulta, não o pedido: um offset além
    do fim é preso à última página, e usar o valor pedido aqui produziria algo como
    "página 4 de 3".
    """
    total = dados["total"]
    offset = dados["offset"]
    return {
        "itens": dados["itens"],
        "total": total,
        "q": q,
        "limite": limite,
        "offset": offset,
        "pagina": offset // limite + 1,
        "paginas": max(1, -(-total // limite)),  # divisão com teto
        "tem_anterior": offset > 0,
        "tem_proxima": offset + limite < total,
        "offset_anterior": max(0, offset - limite),
        "offset_proxima": offset + limite,
    }


@app.get("/salvos", response_class=HTMLResponse)
async def salvos(
    request: Request,
    q: str = Query("", description="Nome, nome real ou vanity; vazio lista os recentes"),
    store: ProfileStore = Depends(get_store),
):
    """Página da base local: busca nos perfis já salvos, sem tocar a Steam."""
    dados = await store.search(q, limite=PAGINA_SALVOS, offset=0)
    return templates.TemplateResponse(
        request,
        "salvos.html",
        {**_page_context(), **_contexto_lista(dados, q, PAGINA_SALVOS)},
    )


@app.get("/salvos/lista", response_class=HTMLResponse)
async def salvos_lista(
    request: Request,
    q: str = Query(""),
    offset: int = Query(0, ge=0),
    store: ProfileStore = Depends(get_store),
):
    """Fragmento htmx: a lista paginada. Destino da busca ao vivo e dos botões."""
    dados = await store.search(q, limite=PAGINA_SALVOS, offset=offset)
    return templates.TemplateResponse(
        request,
        "partials/salvos_lista.html",
        {**_page_context(), **_contexto_lista(dados, q, PAGINA_SALVOS)},
    )


@app.get("/api/salvos")
async def api_salvos(
    q: str = Query("", description="Nome parecido; vazio lista os mais recentes"),
    limite: int = Query(25, ge=1, le=100),
    offset: int = Query(0, ge=0),
    store: ProfileStore = Depends(get_store),
):
    """Consulta os perfis já salvos localmente, sem tocar a Steam.

    Busca por nome parecido via FTS5 — exatamente o que a Steam Web API não
    oferece, possível aqui porque os dados são seus.
    """
    return await store.search(q, limite=limite, offset=offset)


@app.get("/hello", response_class=HTMLResponse)
async def hello(request: Request):
    """Fragmento do walking skeleton — mantido como smoke test da via htmx."""
    return templates.TemplateResponse(request, "partials/hello.html")


@app.get("/health")
async def health(
    cache: AvatarCache = Depends(get_avatar_cache),
    store: ProfileStore = Depends(get_store),
):
    return {
        "status": "ok",
        "steam_api": settings.steam_api_key != "",
        "avatar_cache": cache.stats(),
        "perfis": await store.stats(),
    }
