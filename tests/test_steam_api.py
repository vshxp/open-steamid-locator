"""Cliente da Steam Web API: mapeamento de falhas e proteção da chave."""

import asyncio

import httpx
import pytest
from conftest import (
    BANS_LIMPO,
    CHAVE_FALSA,
    SUMMARY_PUBLICO,
    make_client,
    steam_handler,
)

from src.steam_api import SteamApiError, SteamClient


def build(handler) -> SteamClient:
    return SteamClient(CHAVE_FALSA, make_client(handler))


# ------------------------------------------------------------- caminho felizes


def test_resolve_vanity():
    cliente = build(steam_handler())
    assert asyncio.run(cliente.resolve_vanity("gabelogannewell")) == 76561197960287930


def test_vanity_inexistente_devolve_none():
    """success=42 é 'no match' — ausência, não erro."""
    cliente = build(steam_handler(vanity_steamid=None))
    assert asyncio.run(cliente.resolve_vanity("nao-existe")) is None


def test_player_summary():
    cliente = build(steam_handler())
    assert asyncio.run(cliente.player_summary(1))["personaname"] == "Rabscuttle"


def test_player_summary_ausente_devolve_none():
    cliente = build(steam_handler(summary=None))
    assert asyncio.run(cliente.player_summary(1)) is None


def test_player_bans():
    cliente = build(steam_handler())
    assert asyncio.run(cliente.player_bans(1))["EconomyBan"] == "none"


def test_a_chave_e_enviada_na_query():
    vistos = []

    def handler(request):
        vistos.append(request.url.params.get("key"))
        return httpx.Response(200, json={"response": {"players": [SUMMARY_PUBLICO]}})

    asyncio.run(build(handler).player_summary(1))
    assert vistos == [CHAVE_FALSA]


# --------------------------------------------------- mapeamento de erros HTTP


@pytest.mark.parametrize(
    ("status", "trecho"),
    [
        (401, "recusou a chave"),
        (403, "recusou a chave"),
        (429, "Rate limit"),
        (500, "indisponível"),
        (503, "indisponível"),
        (418, "inesperada"),
    ],
)
def test_status_http_vira_mensagem_legivel(status, trecho):
    cliente = build(steam_handler(status=status))
    with pytest.raises(SteamApiError, match=trecho):
        asyncio.run(cliente.player_summary(1))


def test_corpo_nao_json_levanta_erro_tratado():
    def handler(request):
        return httpx.Response(200, text="<html>manutenção</html>")

    with pytest.raises(SteamApiError, match="não-JSON"):
        asyncio.run(build(handler).player_summary(1))


def test_timeout_vira_erro_tratado():
    def handler(request):
        raise httpx.TimeoutException("estourou", request=request)

    with pytest.raises(SteamApiError, match="Timeout"):
        asyncio.run(build(handler).player_summary(1))


def test_vanity_sem_steamid_na_resposta_levanta():
    def handler(request):
        return httpx.Response(200, json={"response": {"success": 1}})

    with pytest.raises(SteamApiError, match="sem steamid"):
        asyncio.run(build(handler).resolve_vanity("x"))


# ------------- REGRESSÃO: a URL da requisição carrega a API key ---------------
# Interpolar a exceção do httpx na mensagem colocaria a chave no corpo da resposta
# HTTP. Só o nome da classe pode aparecer; o detalhe fica em __cause__, para o log.


def test_excecao_de_rede_nao_expoe_a_chave():
    def handler(request):
        # Simula uma exceção do httpx que inclui a URL completa na mensagem,
        # que é o comportamento de algumas classes e pode mudar entre versões.
        raise httpx.ConnectError(f"falha ao conectar em {request.url}", request=request)

    with pytest.raises(SteamApiError) as exc:
        asyncio.run(build(handler).player_summary(1))

    assert CHAVE_FALSA not in str(exc.value), "a API key vazou na mensagem de erro"
    assert "ConnectError" in str(exc.value), "o tipo do erro deve ser informado"
    assert CHAVE_FALSA in str(exc.value.__cause__), "o detalhe deve seguir em __cause__"


def test_timeout_tambem_nao_expoe_a_chave():
    def handler(request):
        raise httpx.TimeoutException(f"timeout em {request.url}", request=request)

    with pytest.raises(SteamApiError) as exc:
        asyncio.run(build(handler).player_bans(1))
    assert CHAVE_FALSA not in str(exc.value)


@pytest.mark.parametrize("status", [401, 403, 429, 500, 418])
def test_nenhuma_mensagem_de_status_expoe_a_chave(status):
    cliente = build(steam_handler(status=status))
    with pytest.raises(SteamApiError) as exc:
        asyncio.run(cliente.player_summary(1))
    assert CHAVE_FALSA not in str(exc.value)


def test_bans_com_lista_vazia_devolve_none():
    cliente = build(steam_handler(bans=None))
    assert asyncio.run(cliente.player_bans(1)) is None
    assert BANS_LIMPO  # sanidade do fixture importado
