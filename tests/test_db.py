"""Persistência: schema sem duplicação, upsert, FTS e migração.

Vários testes aqui são de regressão para bugs reais desta base — estão marcados
com o motivo, para ninguém "simplificar" o código e reintroduzi-los.
"""

import sqlite3

import pytest
from conftest import BANS_LIMPO, BANS_SUJO, SUMMARY_PRIVADO, SUMMARY_PUBLICO

from src.db import FTS_COLS, ProfileStore, _consulta_fts, documento_canonico

SID = "76561197960287930"
OUTRO = "76561197960265731"


def salvar(store, sid=SID, summary=SUMMARY_PUBLICO, bans=BANS_LIMPO, quando=1000):
    return store._save(sid, documento_canonico(summary, bans), quando)


# ------------------------------------------------------------------ documento


def test_documento_canonico_e_estavel_e_compacto():
    a = documento_canonico({"b": 1, "a": 2}, {"z": 3})
    b = documento_canonico({"a": 2, "b": 1}, {"z": 3})
    assert a == b, "ordem das chaves não deve mudar o documento"
    assert " " not in a, "sem espaços: byte a byte comparável"


def test_documento_canonico_preserva_acentos():
    assert "ção" in documento_canonico({"n": "ção"}, None)


# ---------------------------------------------------------------------- upsert


def test_salvar_e_recuperar(store):
    salvar(store)
    perfil = store._get(SID)
    assert perfil["persona_name"] == "Rabscuttle"
    assert perfil["steamid64"] == SID


def test_reconsulta_nao_duplica_e_preserva_first_seen(store):
    primeiro = salvar(store, quando=1000)
    assert primeiro["novo"] is True

    segundo = salvar(store, quando=2000)
    assert segundo["novo"] is False
    assert segundo["first_seen_at"] == 1000, "first_seen_at deve sobreviver ao update"
    assert segundo["fetched_at"] == 2000

    assert store._stats()["profiles"] == 1


def test_update_sobrescreve_os_campos(store):
    salvar(store)
    salvar(store, summary={**SUMMARY_PUBLICO, "personaname": "NomeNovo"}, quando=2000)
    assert store._get(SID)["persona_name"] == "NomeNovo"
    assert store._stats()["profiles"] == 1


def test_get_de_inexistente_devolve_none(store):
    assert store._get("76561199999999999") is None


# ------------------------------------------------------- colunas geradas


def test_colunas_geradas_projetam_o_json(store):
    salvar(store)
    p = store._get(SID)
    assert p["account_id"] == 22202
    assert p["real_name"] == "Gabe Newell"
    assert p["country"] == "US"
    assert p["visibility"] == 3
    assert p["created_at"] == 1063192486
    assert p["clan_id"] == "103582791429521412"
    assert p["vac_banned"] == 0
    assert p["economy_ban"] == "none"


def test_colunas_geradas_de_ban_refletem_documento_sujo(store):
    salvar(store, bans=BANS_SUJO)
    p = store._get(SID)
    assert p["vac_banned"] == 1
    assert p["vac_ban_count"] == 2
    assert p["game_ban_count"] == 1
    assert p["economy_ban"] == "banned"


def test_vanity_extraida_de_url_id(store):
    salvar(store)
    assert store._get(SID)["vanity"] == "GabeLoganNewell"


def test_vanity_e_nula_para_url_de_profiles(store):
    """/profiles/<steamid64> não carrega vanity — deve virar NULL, não string vazia."""
    salvar(store, summary=SUMMARY_PRIVADO)
    assert store._get(SID)["vanity"] is None


def test_coluna_gerada_virtual_nao_ocupa_espaco(tmp_path):
    """O ponto do schema: projeção custa zero bytes."""
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE so_json (raw TEXT)")
    con.execute(
        "CREATE TABLE com_geradas (raw TEXT, nome TEXT GENERATED ALWAYS AS "
        "(json_extract(raw, '$.n')) VIRTUAL)"
    )
    doc = '{"n":"' + "x" * 50 + '"}'
    con.executemany("INSERT INTO so_json VALUES (?)", [(doc,)] * 500)
    con.executemany("INSERT INTO com_geradas (raw) VALUES (?)", [(doc,)] * 500)

    def tamanho(tabela):
        return con.execute("SELECT SUM(pgsize) FROM dbstat WHERE name = ?", (tabela,)).fetchone()[0]

    assert tamanho("so_json") == tamanho("com_geradas")


def test_indices_sao_usados_apesar_de_geradas(store):
    """Índice sobre coluna gerada virtual precisa ser aproveitado pelo planner."""
    salvar(store)
    with store._connect() as con:
        for coluna, indice in (
            ("persona_name", "idx_perfil_nome"),
            ("vanity", "idx_perfil_vanity"),
            ("country", "idx_perfil_pais"),
        ):
            plano = con.execute(
                f"EXPLAIN QUERY PLAN SELECT steamid64 FROM perfil WHERE {coluna} = 'x'"
            ).fetchone()[-1]
            assert indice in plano, f"{coluna}: planner não usou {indice} ({plano})"


# ------------------------------------------------------------------------ FTS


@pytest.mark.parametrize(
    ("termo", "acha"),
    [
        ("rabs", True),  # persona name
        ("Gabe", True),  # nome real "Gabe Newell"
        ("gabelogan", True),  # vanity GabeLoganNewell
        ("newell", True),  # 2ª palavra do nome real
        ("RABSCUTTLE", True),  # busca não diferencia caixa
        ("zzzz", False),
        ("abscuttle", False),  # prefixo, não substring
    ],
)
def test_busca_cobre_persona_real_e_vanity(store, termo, acha):
    salvar(store)
    resultado = store._search(termo, 10, 0)
    assert (resultado["total"] == 1) is acha, f"termo {termo!r}"


def test_busca_vazia_lista_os_mais_recentes(store):
    salvar(store, sid=SID, quando=1000)
    salvar(store, sid=OUTRO, summary={**SUMMARY_PUBLICO, "steamid": OUTRO}, quando=2000)
    r = store._search("", 10, 0)
    assert r["total"] == 2
    assert r["itens"][0]["steamid64"] == OUTRO, "mais recente primeiro"


def test_fts_sincroniza_no_update(store):
    """Trigger de UPDATE precisa remover o valor antigo do índice."""
    salvar(store)
    assert store._search("rabs", 10, 0)["total"] == 1

    salvar(store, summary={**SUMMARY_PUBLICO, "personaname": "Zephyr"}, quando=2000)
    assert store._search("rabs", 10, 0)["total"] == 0, "nome antigo ficou no índice"
    assert store._search("zeph", 10, 0)["total"] == 1


def test_fts_sincroniza_no_delete(store):
    salvar(store)
    with store._connect() as con:
        con.execute("DELETE FROM perfil WHERE steamid64 = ?", (SID,))
    assert store._search("rabs", 10, 0)["total"] == 0


def test_ddl_do_fts_cobre_exatamente_fts_cols(store):
    with store._connect() as con:
        colunas = tuple(r["name"] for r in con.execute("PRAGMA table_info(perfil_fts)"))
    assert colunas == FTS_COLS


# ------------------------------ REGRESSÃO: injeção na sintaxe do FTS5 ----------
# Uma aspa no termo fechava a string literal e o resto virava operador, gerando
# OperationalError e HTTP 500.


@pytest.mark.parametrize(
    "termo",
    [
        'a" OR persona_name : "b',
        '"',
        '""',
        "*",
        "NEAR(",
        'EJ" OR "1',
        "^:-",
        "a AND b",
        "x" * 500,
    ],
)
def test_termo_hostil_nao_levanta_e_nao_vaza(store, termo):
    salvar(store)
    resultado = store._search(termo, 10, 0)
    assert resultado["total"] == 0, f"{termo!r} não deveria casar nada"
    assert resultado["itens"] == []


def test_consulta_fts_dobra_aspas():
    assert _consulta_fts('x"y') == '"x""y"*'
    assert _consulta_fts("a b") == '"a"* "b"*'
    assert _consulta_fts("   ") == ""


# ------------------------------ REGRESSÃO: offset fora de faixa ---------------
# offset vem da URL; sem clamp a interface exibia "página 4 de 3" com lista vazia.


@pytest.mark.parametrize("pedido", [0, 3, 6, 9, 99, 10**9])
def test_offset_e_preso_a_ultima_pagina(store, pedido):
    for i in range(7):
        sid = str(76561197960265731 + i)
        salvar(store, sid=sid, summary={**SUMMARY_PUBLICO, "steamid": sid}, quando=1000 + i)

    r = store._search("", 3, pedido)
    assert r["total"] == 7
    assert r["offset"] <= 6, "offset efetivo não pode passar da última página"
    assert r["offset"] % 3 == 0, "deve cair no início de uma página"
    if pedido <= 6:
        assert r["offset"] == pedido
    assert r["itens"], "a última página tem conteúdo"


def test_offset_em_base_vazia(store):
    r = store._search("", 10, 500)
    assert r == {"total": 0, "offset": 0, "itens": []}


def test_search_sempre_devolve_offset(store):
    """A interface calcula a paginação a partir deste campo; nunca pode faltar."""
    salvar(store)
    for termo in ("", "rabs", "zzz", '"'):
        assert "offset" in store._search(termo, 10, 0)


# ------------------------------ REGRESSÃO: migração idempotente ---------------
# PRAGMA table_info OMITE colunas geradas. Usá-lo na detecção fazia a migração
# tentar adicionar `vanity` a cada boot e falhar com "duplicate column name",
# derrubando a aplicação na inicialização.

PRE_VANITY_DDL = """
CREATE TABLE perfil (
  steamid64     TEXT NOT NULL UNIQUE,
  raw           TEXT NOT NULL,
  fetched_at    INTEGER NOT NULL,
  first_seen_at INTEGER NOT NULL,
  persona_name TEXT GENERATED ALWAYS AS (json_extract(raw, '$.summary.personaname')) VIRTUAL,
  real_name    TEXT GENERATED ALWAYS AS (json_extract(raw, '$.summary.realname')) VIRTUAL
);
CREATE VIRTUAL TABLE perfil_fts USING fts5(
  persona_name, real_name, content='perfil', content_rowid='rowid');
"""


def test_pragma_table_info_omite_colunas_geradas():
    """Documenta a armadilha que causou o bug — se o SQLite mudar, o teste avisa."""
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE t (a TEXT, b TEXT GENERATED ALWAYS AS (a) VIRTUAL)")
    info = [r[1] for r in con.execute("PRAGMA table_info(t)")]
    xinfo = [r[1] for r in con.execute("PRAGMA table_xinfo(t)")]
    assert info == ["a"]
    assert "b" in xinfo


def test_migracao_e_idempotente(tmp_path):
    caminho = tmp_path / "perfis.sqlite3"
    ProfileStore(caminho)
    ProfileStore(caminho)  # 2º boot: não deve levantar
    store = ProfileStore(caminho)  # 3º, por garantia
    salvar(store)
    assert store._search("rabs", 10, 0)["total"] == 1


def test_migracao_de_banco_sem_vanity(tmp_path):
    """Banco antigo ganha a coluna, o índice textual é recriado e repovoado."""
    caminho = tmp_path / "perfis.sqlite3"
    con = sqlite3.connect(caminho)
    con.executescript(PRE_VANITY_DDL)
    con.execute(
        "INSERT INTO perfil (steamid64, raw, fetched_at, first_seen_at) VALUES (?,?,?,?)",
        (SID, documento_canonico(SUMMARY_PUBLICO, BANS_LIMPO), 1000, 1000),
    )
    con.commit()
    con.close()

    store = ProfileStore(caminho)

    assert store._get(SID)["vanity"] == "GabeLoganNewell"
    with store._connect() as c:
        assert tuple(r["name"] for r in c.execute("PRAGMA table_info(perfil_fts)")) == FTS_COLS
    assert store._search("gabelogan", 10, 0)["total"] == 1, "índice não foi repovoado"
    assert store._stats()["profiles"] == 1, "a linha existente foi preservada"


def test_migracao_preserva_dados_em_boot_repetido(tmp_path):
    caminho = tmp_path / "perfis.sqlite3"
    salvar(ProfileStore(caminho))
    store = ProfileStore(caminho)
    assert store._stats()["profiles"] == 1
    assert store._get(SID)["first_seen_at"] == 1000


# ------------------------------------------------------------------- estatísticas


def test_stats_conta_perfis_e_bytes(store):
    assert store._stats()["profiles"] == 0
    salvar(store)
    stats = store._stats()
    assert stats["profiles"] == 1
    assert stats["bytes"] > 0
