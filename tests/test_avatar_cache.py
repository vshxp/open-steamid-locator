"""Cache de avatares: validação do hash, download, gravação atômica."""

import asyncio

import httpx
import pytest
from conftest import HASH_VALIDO, JPEG_FALSO, avatar_handler, make_client

from src.avatar_cache import (
    AVATAR_HASH_RE,
    MAX_BYTES,
    AvatarCache,
    AvatarCacheError,
)


def build(tmp_path, handler) -> AvatarCache:
    return AvatarCache(tmp_path / "avatars", make_client(handler))


# ------------------ SEGURANÇA: o hash entra em URL e em nome de arquivo -------


@pytest.mark.parametrize(
    "hash_ruim",
    [
        "../../etc/passwd",
        "..%2F..%2Fetc%2Fpasswd",
        "a" * 39,  # curto
        "a" * 41,  # longo
        "A" * 40,  # maiúsculas: formato da Steam é minúsculo
        "g" * 40,  # 'g' não é hex
        "a" * 20 + "/" + "a" * 19,
        "",
        "a" * 20 + "\x00" + "a" * 19,
        "a" * 20 + "\n" + "a" * 19,
    ],
)
def test_hash_invalido_e_rejeitado_pelo_regex(hash_ruim):
    assert AVATAR_HASH_RE.match(hash_ruim) is None


def test_hash_valido_e_aceito():
    assert AVATAR_HASH_RE.match(HASH_VALIDO)
    assert AVATAR_HASH_RE.match("0123456789abcdef" * 2 + "01234567")


# ------------------------------------------------------------- cache hit/miss


def test_cached_e_none_antes_do_download(tmp_path):
    assert build(tmp_path, avatar_handler()).cached(HASH_VALIDO) is None


def test_fetch_grava_e_depois_e_hit(tmp_path):
    cache = build(tmp_path, avatar_handler())
    caminho = asyncio.run(cache.fetch(HASH_VALIDO))
    assert caminho.read_bytes() == JPEG_FALSO
    assert cache.cached(HASH_VALIDO) == caminho


def test_arquivo_vazio_nao_conta_como_cache(tmp_path):
    cache = build(tmp_path, avatar_handler())
    caminho = cache.directory / f"{HASH_VALIDO}_full.jpg"
    caminho.write_bytes(b"")
    assert cache.cached(HASH_VALIDO) is None


def test_permissao_do_arquivo_permite_leitura(tmp_path):
    """NamedTemporaryFile cria 0600; um cache servido precisa ser legível."""
    cache = build(tmp_path, avatar_handler())
    caminho = asyncio.run(cache.fetch(HASH_VALIDO))
    assert caminho.stat().st_mode & 0o044, "outros usuários não conseguem ler"


def test_gravacao_nao_deixa_arquivo_temporario(tmp_path):
    cache = build(tmp_path, avatar_handler())
    asyncio.run(cache.fetch(HASH_VALIDO))
    restos = [p.name for p in cache.directory.iterdir() if p.name.endswith(".tmp")]
    assert restos == [], f"temporários órfãos: {restos}"


def test_fetch_repetido_e_idempotente(tmp_path):
    cache = build(tmp_path, avatar_handler())
    asyncio.run(cache.fetch(HASH_VALIDO))
    asyncio.run(cache.fetch(HASH_VALIDO))
    assert len(list(cache.directory.glob("*_full.jpg"))) == 1


# ----------------------------------------------- conteúdo que não deve entrar


def test_conteudo_sem_magic_bytes_de_jpeg_e_rejeitado(tmp_path):
    """Página de erro com HTTP 200 não pode virar arquivo em cache."""
    cache = build(tmp_path, avatar_handler(conteudo=b"<html>404</html>"))
    with pytest.raises(AvatarCacheError, match="não é JPEG"):
        asyncio.run(cache.fetch(HASH_VALIDO))
    assert list(cache.directory.iterdir()) == []


def test_conteudo_grande_e_rejeitado(tmp_path):
    grande = JPEG_FALSO + b"\x00" * (MAX_BYTES + 1)
    cache = build(tmp_path, avatar_handler(conteudo=grande))
    with pytest.raises(AvatarCacheError, match="acima do limite"):
        asyncio.run(cache.fetch(HASH_VALIDO))
    assert list(cache.directory.iterdir()) == []


@pytest.mark.parametrize("status", [404, 403, 429, 500, 503])
def test_status_de_erro_da_cdn_levanta(tmp_path, status):
    cache = build(tmp_path, avatar_handler(status=status))
    with pytest.raises(AvatarCacheError, match=str(status)):
        asyncio.run(cache.fetch(HASH_VALIDO))
    assert list(cache.directory.iterdir()) == []


def test_timeout_de_rede_levanta_erro_tratado(tmp_path):
    def handler(request):
        raise httpx.TimeoutException("estourou", request=request)

    cache = build(tmp_path, handler)
    with pytest.raises(AvatarCacheError, match="Timeout"):
        asyncio.run(cache.fetch(HASH_VALIDO))


def test_falha_de_rede_levanta_erro_tratado(tmp_path):
    def handler(request):
        raise httpx.ConnectError("sem rota", request=request)

    cache = build(tmp_path, handler)
    with pytest.raises(AvatarCacheError):
        asyncio.run(cache.fetch(HASH_VALIDO))


# ------------------------------------------------------------------ estatísticas


def test_stats_reflete_o_conteudo(tmp_path):
    cache = build(tmp_path, avatar_handler())
    assert cache.stats() == {"files": 0, "bytes": 0}
    asyncio.run(cache.fetch(HASH_VALIDO))
    stats = cache.stats()
    assert stats["files"] == 1
    assert stats["bytes"] == len(JPEG_FALSO)


def test_diretorio_e_criado_na_construcao(tmp_path):
    destino = tmp_path / "fundo" / "avatars"
    AvatarCache(destino, make_client(avatar_handler()))
    assert destino.is_dir()
