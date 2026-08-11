"""Conversão de SteamID: aritmética pura, sem I/O."""

import pytest

from src.steamid import (
    ACCOUNT_ID_MAX,
    STEAM64_BASE,
    SteamIdError,
    parse_query,
    to_identity,
)

# Vetor clássico: account ID 22202. Todas as formas abaixo são o mesmo perfil.
ESPERADO = 76561197960287930

CONVERGENTES = [
    "76561197960287930",
    "STEAM_0:0:11101",
    "STEAM_1:0:11101",
    "[U:1:22202]",
    "U:1:22202",
    "22202",
    "https://steamcommunity.com/profiles/76561197960287930",
    "steamcommunity.com/profiles/76561197960287930/",
    "  76561197960287930  ",
]


@pytest.mark.parametrize("entrada", CONVERGENTES)
def test_formatos_convergem_para_o_mesmo_steamid64(entrada):
    assert parse_query(entrada).steamid64 == ESPERADO


@pytest.mark.parametrize(
    ("entrada", "detectado"),
    [
        ("76561197960287930", "steamid64"),
        ("STEAM_0:0:11101", "steamid2"),
        ("[U:1:22202]", "steamid3"),
        ("22202", "account_id"),
        ("https://steamcommunity.com/profiles/76561197960287930", "url_perfil"),
        ("https://steamcommunity.com/id/gabelogannewell", "url_vanity"),
        ("gabelogannewell", "vanity"),
    ],
)
def test_formato_detectado(entrada, detectado):
    assert parse_query(entrada).detected == detectado


@pytest.mark.parametrize("campo", ["steamid2", "steamid2_legacy", "steamid3"])
def test_round_trip_64_para_formato_e_volta(campo):
    valor = getattr(to_identity(ESPERADO), campo)
    assert parse_query(valor).steamid64 == ESPERADO


def test_decomposicao_de_bits():
    ident = to_identity(ESPERADO)
    assert ident.account_id == 22202
    assert ident.universe == 1
    assert ident.account_type == 1
    assert ident.instance == 1
    assert ident.universe_name == "público"
    assert ident.account_type_name == "individual"
    assert ident.steamid2 == "STEAM_1:0:11101"
    assert ident.steamid2_legacy == "STEAM_0:0:11101"
    assert ident.steamid3 == "[U:1:22202]"
    assert ident.profile_url.endswith(str(ESPERADO))


def test_steamid64_serializado_como_string():
    """17 dígitos passam de 2^53: número em JSON perderia precisão no browser."""
    ident = to_identity(ESPERADO)
    assert isinstance(ident.steamid64, str)
    assert isinstance(ident.as_dict()["steamid64"], str)


def test_paridade_impar():
    """account_id ímpar cai no bit Y do SteamID2."""
    ident = to_identity(STEAM64_BASE + 3)
    assert ident.steamid2 == "STEAM_1:1:1"
    assert parse_query("STEAM_1:1:1").steamid64 == STEAM64_BASE + 3


def test_vanity_nao_resolve_sozinho():
    parsed = parse_query("gabelogannewell")
    assert parsed.steamid64 is None
    assert parsed.vanity == "gabelogannewell"


@pytest.mark.parametrize(
    "entrada",
    [
        "",
        "   ",
        "!!!",
        "0",
        str(STEAM64_BASE),  # base exata = account_id 0, não é conta
        "STEAM_0:2:1",  # bit de paridade só pode ser 0 ou 1
        "STEAM_9:0:1",  # universo fora da faixa
        "https://exemplo.com/x",
        "https://steamcommunity.com/algo/outro",
        "[U:1:0]",
        "nome com espaco",
        "a",  # curto demais para vanity
        "x" * 40,  # longo demais para vanity
    ],
)
def test_entradas_invalidas_levantam(entrada):
    with pytest.raises(SteamIdError):
        parse_query(entrada)


def test_account_id_no_limite_superior():
    assert parse_query(str(ACCOUNT_ID_MAX)).steamid64 == STEAM64_BASE + ACCOUNT_ID_MAX


def test_numero_acima_do_limite_de_account_id_vira_steamid64():
    """Um inteiro grande é SteamID64, não account ID — e é validado como tal."""
    with pytest.raises(SteamIdError):
        parse_query(str(ACCOUNT_ID_MAX + 1))


def test_mensagem_de_erro_orienta_o_usuario():
    with pytest.raises(SteamIdError) as exc:
        parse_query("!!!")
    assert "SteamID64" in str(exc.value)
