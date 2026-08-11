"""Filtros de formatação usados nos templates."""

import pytest

from src.main import _fmt_ano, _fmt_bandeira, _fmt_datahora, _fmt_idade


@pytest.mark.parametrize(
    ("codigo", "esperado"),
    [
        ("BR", "🇧🇷"),
        ("US", "🇺🇸"),
        ("us", "🇺🇸"),
        ("", ""),
        (None, ""),
        ("USA", ""),
        ("1", ""),
        ("U1", ""),
    ],
)
def test_bandeira(codigo, esperado):
    assert _fmt_bandeira(codigo) == esperado


def test_datahora_formata_iso():
    assert _fmt_datahora("2003-09-10T11:14:46+00:00") == "10/09/2003 às 11:14 UTC"


@pytest.mark.parametrize("entrada", [None, ""])
def test_datahora_vazia_vira_travessao(entrada):
    assert _fmt_datahora(entrada) == "—"


def test_datahora_invalida_devolve_a_entrada():
    """Melhor mostrar o valor cru que quebrar a página."""
    assert _fmt_datahora("não é data") == "não é data"


def test_ano_extrai_do_timestamp():
    assert _fmt_ano(1063192486) == "2003"


@pytest.mark.parametrize("entrada", [None, 0])
def test_ano_vazio(entrada):
    assert _fmt_ano(entrada) == ""


def test_ano_de_timestamp_absurdo_nao_quebra():
    assert _fmt_ano(10**20) == ""


def test_idade_de_data_futura_e_vazia():
    assert _fmt_idade("2999-01-01T00:00:00+00:00") == ""


@pytest.mark.parametrize("entrada", [None, "", "lixo"])
def test_idade_invalida_e_vazia(entrada):
    assert _fmt_idade(entrada) == ""


def test_idade_de_data_antiga_menciona_anos():
    resultado = _fmt_idade("2003-09-10T11:14:46+00:00")
    assert resultado.startswith("há ")
    assert "ano" in resultado
