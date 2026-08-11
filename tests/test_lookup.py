"""Orquestração: parse → converte → enriquece, e o que acontece quando falha."""

import asyncio

import pytest
from conftest import (
    BANS_LIMPO,
    BANS_SUJO,
    CHAVE_FALSA,
    SUMMARY_PRIVADO,
    SUMMARY_PUBLICO,
    make_client,
    steam_handler,
)

from src.lookup import _url_segura, lookup
from src.steam_api import SteamClient
from src.steamid import SteamIdError

SID = "76561197960287930"


def cliente(handler) -> SteamClient:
    return SteamClient(CHAVE_FALSA, make_client(handler))


def rodar(query, handler=None):
    return asyncio.run(lookup(query, cliente(handler or steam_handler())))


# ------------------------------------------------------------ sem API key


def test_sem_chave_converte_mas_marca_skipped():
    r = asyncio.run(lookup(SID, None))
    assert r["steamid"]["steamid64"] == SID
    assert r["steam_api"]["status"] == "skipped"
    assert "STEAM_API_KEY" in r["steam_api"]["reason"]


def test_sem_chave_vanity_e_erro_explicativo():
    with pytest.raises(SteamIdError, match="STEAM_API_KEY"):
        asyncio.run(lookup("gabelogannewell", None))


def test_entrada_invalida_levanta_antes_de_qualquer_rede():
    with pytest.raises(SteamIdError):
        asyncio.run(lookup("!!!", None))


# ------------------------------------------------------------- caminho ok


def test_status_ok_traz_interpretado_e_cru():
    r = rodar(SID)
    api = r["steam_api"]
    assert api["status"] == "ok"
    assert api["interpreted"]["persona_name"] == "Rabscuttle"
    assert api["interpreted"]["visibility"] == "público"
    assert api["interpreted"]["country"] == "US"
    assert api["interpreted"]["avatar_hash"] == SUMMARY_PUBLICO["avatarhash"]
    assert api["raw"]["summary"]["personaname"] == "Rabscuttle"
    assert api["raw"]["bans"] == BANS_LIMPO


def test_timestamps_viram_iso():
    interp = rodar(SID)["steam_api"]["interpreted"]
    assert interp["account_created_at"].startswith("2003-09-10T")
    assert interp["last_logoff_at"] is not None


def test_vanity_resolve_com_chave():
    r = rodar("gabelogannewell")
    assert r["query"]["detected"] == "vanity"
    assert r["steamid"]["steamid64"] == SID


def test_vanity_inexistente_levanta():
    with pytest.raises(SteamIdError, match="Nenhum perfil"):
        rodar("nao-existe", steam_handler(vanity_steamid=None))


def test_bans_limpos_marcados_como_clean():
    bans = rodar(SID)["steam_api"]["interpreted"]["bans"]
    assert bans["clean"] is True
    assert bans["days_since_last_ban"] is None, "sem ban, o contador não faz sentido"


def test_bans_sujos_expostos():
    bans = rodar(SID, steam_handler(bans=BANS_SUJO))["steam_api"]["interpreted"]["bans"]
    assert bans["clean"] is False
    assert bans["vac_banned"] is True
    assert bans["vac_ban_count"] == 2
    assert bans["game_ban_count"] == 1
    assert bans["days_since_last_ban"] == 137


# --------------- privacidade falha em silêncio: 200 com campos ausentes -------


def test_perfil_privado_recebe_aviso_explicito():
    r = rodar(SID, steam_handler(summary=SUMMARY_PRIVADO))
    interp = r["steam_api"]["interpreted"]
    assert interp["visibility"] == "privado"
    assert "não pude ver" in interp["aviso"]
    assert interp["account_created_at"] is None
    assert interp["country"] is None


def test_perfil_publico_nao_recebe_aviso():
    assert "aviso" not in rodar(SID)["steam_api"]["interpreted"]


# ------------------------------------------------------- degradação graciosa


def test_erro_da_api_nao_impede_a_conversao():
    r = rodar(SID, steam_handler(status=429))
    assert r["steamid"]["steamid2"] == "STEAM_1:0:11101", "conversão deve sobreviver"
    assert r["steam_api"]["status"] == "error"
    assert "Rate limit" in r["steam_api"]["reason"]


def test_perfil_ausente_e_not_found():
    r = rodar(SID, steam_handler(summary=None, bans=None))
    assert r["steam_api"]["status"] == "not_found"
    assert r["steamid"]["account_id"] == 22202


def test_erro_da_api_nunca_expoe_a_chave():
    r = rodar(SID, steam_handler(status=401))
    assert CHAVE_FALSA not in str(r)


# ------------------- guarda de esquema no href do template --------------------


@pytest.mark.parametrize(
    ("entrada", "saida"),
    [
        ("https://steamcommunity.com/id/x/", "https://steamcommunity.com/id/x/"),
        ("http://exemplo.com", "http://exemplo.com"),
        ("javascript:alert(1)", None),
        ("JavaScript:alert(1)", None),
        ("data:text/html,<script>", None),
        ("vbscript:msgbox", None),
        ("//exemplo.com", None),
        ("", None),
        (None, None),
    ],
)
def test_url_segura_so_aceita_http(entrada, saida):
    assert _url_segura(entrada) == saida


def test_custom_url_do_perfil_passa_pela_guarda():
    hostil = {**SUMMARY_PUBLICO, "profileurl": "javascript:alert(1)"}
    r = rodar(SID, steam_handler(summary=hostil))
    assert r["steam_api"]["interpreted"]["custom_url"] is None
    assert r["steam_api"]["raw"]["summary"]["profileurl"] == "javascript:alert(1)", (
        "o valor cru deve ser preservado para auditoria"
    )
