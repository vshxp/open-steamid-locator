"""Fixtures compartilhadas.

Duas garantias que a suíte inteira depende:

1. **Nenhum teste toca a rede.** Todo cliente HTTP usa `httpx.MockTransport`. A
   STEAM_API_KEY definida aqui é falsa e nunca sai da máquina.
2. **Nenhum teste toca os dados reais.** CACHE_DIR e DATA_DIR apontam para um
   diretório temporário, definido *antes* de importar `src.config` — o objeto
   `settings` é criado no import, então configurar depois não teria efeito.
"""

import os
import shutil
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="osl-tests-"))

# Precede qualquer import de src.*. Variável de ambiente tem precedência sobre o
# arquivo .env no pydantic-settings, então um .env real no diretório não vaza para cá.
os.environ["APP_NAME"] = "open-steamid-locator-test"
os.environ["CACHE_DIR"] = str(_TMP / "cache")
os.environ["DATA_DIR"] = str(_TMP / "data")
os.environ["DEBUG"] = "false"
os.environ["STEAM_API_KEY"] = CHAVE_FALSA = "dEadbEef00112233445566778899aabb"

import httpx  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from src.avatar_cache import AvatarCache  # noqa: E402
from src.db import ProfileStore  # noqa: E402
from src.steam_api import SteamClient  # noqa: E402

# JPEG mínimo: os magic bytes que o cache exige, mais um fim de imagem.
JPEG_FALSO = b"\xff\xd8\xff\xe0" + b"\x00" * 64 + b"\xff\xd9"

HASH_VALIDO = "a" * 40
HASH_OUTRO = "b" * 40

SUMMARY_PUBLICO = {
    "steamid": "76561197960287930",
    "personaname": "Rabscuttle",
    "realname": "Gabe Newell",
    "profileurl": "https://steamcommunity.com/id/GabeLoganNewell/",
    "avatarfull": "https://avatars.steamstatic.com/x_full.jpg",
    "avatarhash": HASH_VALIDO,
    "communityvisibilitystate": 3,
    "personastate": 1,
    "timecreated": 1063192486,
    "lastlogoff": 1700000000,
    "loccountrycode": "US",
    "primaryclanid": "103582791429521412",
}

SUMMARY_PRIVADO = {
    "steamid": "76561197960287930",
    "personaname": "Escondido",
    "profileurl": "https://steamcommunity.com/profiles/76561197960287930/",
    "avatarhash": HASH_OUTRO,
    "communityvisibilitystate": 1,
    "personastate": 0,
}

BANS_LIMPO = {
    "SteamId": "76561197960287930",
    "CommunityBanned": False,
    "VACBanned": False,
    "NumberOfVACBans": 0,
    "DaysSinceLastBan": 0,
    "NumberOfGameBans": 0,
    "EconomyBan": "none",
}

BANS_SUJO = {
    "SteamId": "76561197960287930",
    "CommunityBanned": False,
    "VACBanned": True,
    "NumberOfVACBans": 2,
    "DaysSinceLastBan": 137,
    "NumberOfGameBans": 1,
    "EconomyBan": "banned",
}


def pytest_sessionfinish(session, exitstatus):
    shutil.rmtree(_TMP, ignore_errors=True)


# ---------------------------------------------------------------- transportes


def steam_handler(
    *,
    summary: dict | None = SUMMARY_PUBLICO,
    bans: dict | None = BANS_LIMPO,
    vanity_steamid: str | None = "76561197960287930",
    status: int = 200,
):
    """Handler de MockTransport que imita a Steam Web API.

    `summary=None` simula perfil ausente (lista vazia), que é como a Steam
    responde para SteamID inexistente — com HTTP 200, não erro.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        caminho = request.url.path
        if status != 200:
            return httpx.Response(status, text="erro")
        if "ResolveVanityURL" in caminho:
            if vanity_steamid is None:
                return httpx.Response(200, json={"response": {"success": 42}})
            return httpx.Response(200, json={"response": {"success": 1, "steamid": vanity_steamid}})
        if "GetPlayerSummaries" in caminho:
            return httpx.Response(200, json={"response": {"players": [summary] if summary else []}})
        if "GetPlayerBans" in caminho:
            return httpx.Response(200, json={"players": [bans] if bans else []})
        return httpx.Response(404, text="rota não mockada")

    return handler


def avatar_handler(*, conteudo: bytes = JPEG_FALSO, status: int = 200):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, content=conteudo)

    return handler


def make_client(handler) -> httpx.AsyncClient:
    """AsyncClient sem rede. Não precisa de aclose: MockTransport não abre socket."""
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


# -------------------------------------------------------------------- fixtures


@pytest.fixture
def store(tmp_path) -> ProfileStore:
    return ProfileStore(tmp_path / "perfis.sqlite3")


@pytest.fixture
def cache(tmp_path) -> AvatarCache:
    return AvatarCache(tmp_path / "avatars", make_client(avatar_handler()))


@pytest.fixture
def steam() -> SteamClient:
    return SteamClient(CHAVE_FALSA, make_client(steam_handler()))


@pytest.fixture
def client(store, cache, steam):
    """TestClient com as três dependências externas substituídas.

    A aplicação real é exercitada de ponta a ponta; só as fronteiras de I/O
    (Steam, CDN, disco) são redirecionadas para o temporário e o mock.
    """
    from src import main

    main.app.dependency_overrides[main.get_store] = lambda: store
    main.app.dependency_overrides[main.get_avatar_cache] = lambda: cache
    main.app.dependency_overrides[main.get_steam_client] = lambda: steam
    with TestClient(main.app) as c:
        yield c
    main.app.dependency_overrides.clear()


@pytest.fixture
def client_sem_chave(store, cache):
    """Como `client`, mas sem Steam Web API key — só conversão de SteamID."""
    from src import main

    main.app.dependency_overrides[main.get_store] = lambda: store
    main.app.dependency_overrides[main.get_avatar_cache] = lambda: cache
    main.app.dependency_overrides[main.get_steam_client] = lambda: None
    with TestClient(main.app) as c:
        yield c
    main.app.dependency_overrides.clear()
