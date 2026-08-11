"""Persistência dos perfis em SQLite.

Princípio do schema: **o dado vive uma única vez**, dentro da coluna `raw`, que
guarda a resposta da Steam Web API exatamente como veio. Todos os campos
consultáveis são colunas *geradas virtuais* — computadas na leitura, ocupando
zero bytes em disco. Não há cópia normalizada ao lado do JSON.

O mesmo vale para a busca textual: a tabela FTS5 usa `content='perfil'`, ou seja
lê do próprio perfil em vez de guardar uma segunda cópia dos nomes.

Modelo temporal: apenas o **estado atual**. Cada consulta sobrescreve o perfil
(`fetched_at` avança, `first_seen_at` é preservado). Não há histórico de
mudanças — decisão explícita do projeto. Se um dia quiser histórico, o caminho é
uma tabela `observacao(steamid64, em, hash)` referenciando estados distintos por
hash de conteúdo; o `raw` já está no formato canônico necessário para isso.

A foto do perfil não fica aqui: o avatar é gravado em disco pelo AvatarCache,
endereçado pelo próprio `avatarhash`. Guardar o binário no banco duplicaria o que
já está deduplicado por construção no filesystem. O banco referencia o hash.
"""

import asyncio
import json
import sqlite3
from pathlib import Path

_PROFILE_URL = "json_extract(raw, '$.summary.profileurl')"

# Extrai a vanity de uma profileurl. Só URLs /id/<vanity> têm vanity; /profiles/
# carrega o SteamID64 e não rende nada. A vanity não contém barra, então remover
# as barras do trecho final basta para limpar a barra terminal.
VANITY_EXPR = f"""
CASE WHEN {_PROFILE_URL} LIKE '%/id/%'
     THEN replace(substr({_PROFILE_URL}, instr({_PROFILE_URL}, '/id/') + 4), '/', '')
END
"""

# Não se usa STRICT aqui de propósito: sob STRICT, o tipo declarado de uma coluna
# gerada é verificado contra o que json_extract devolve, e a Steam varia o tipo de
# alguns campos entre número e string. A integridade vem de todas as escritas
# passarem por save(), não do banco.
SCHEMA_PERFIL = f"""
CREATE TABLE IF NOT EXISTS perfil (
  steamid64     TEXT NOT NULL UNIQUE,
  raw           TEXT NOT NULL,
  fetched_at    INTEGER NOT NULL,
  first_seen_at INTEGER NOT NULL,

  -- Projeções sobre `raw`. VIRTUAL = computadas na leitura, zero armazenamento.
  account_id   INTEGER GENERATED ALWAYS AS (CAST(steamid64 AS INTEGER) - 76561197960265728) VIRTUAL,
  persona_name TEXT GENERATED ALWAYS AS (json_extract(raw, '$.summary.personaname')) VIRTUAL,
  real_name    TEXT GENERATED ALWAYS AS (json_extract(raw, '$.summary.realname')) VIRTUAL,
  avatar_hash  TEXT GENERATED ALWAYS AS (json_extract(raw, '$.summary.avatarhash')) VIRTUAL,
  country      TEXT GENERATED ALWAYS AS (json_extract(raw, '$.summary.loccountrycode')) VIRTUAL,
  profile_url  TEXT GENERATED ALWAYS AS ({_PROFILE_URL}) VIRTUAL,
  vanity       TEXT GENERATED ALWAYS AS ({VANITY_EXPR}) VIRTUAL,
  visibility   INTEGER GENERATED ALWAYS AS (json_extract(raw, '$.summary.communityvisibilitystate')) VIRTUAL,
  persona_state INTEGER GENERATED ALWAYS AS (json_extract(raw, '$.summary.personastate')) VIRTUAL,
  created_at   INTEGER GENERATED ALWAYS AS (json_extract(raw, '$.summary.timecreated')) VIRTUAL,
  last_logoff  INTEGER GENERATED ALWAYS AS (json_extract(raw, '$.summary.lastlogoff')) VIRTUAL,
  clan_id      TEXT GENERATED ALWAYS AS (json_extract(raw, '$.summary.primaryclanid')) VIRTUAL,

  vac_banned       INTEGER GENERATED ALWAYS AS (json_extract(raw, '$.bans.VACBanned')) VIRTUAL,
  vac_ban_count    INTEGER GENERATED ALWAYS AS (json_extract(raw, '$.bans.NumberOfVACBans')) VIRTUAL,
  game_ban_count   INTEGER GENERATED ALWAYS AS (json_extract(raw, '$.bans.NumberOfGameBans')) VIRTUAL,
  community_banned INTEGER GENERATED ALWAYS AS (json_extract(raw, '$.bans.CommunityBanned')) VIRTUAL,
  economy_ban      TEXT GENERATED ALWAYS AS (json_extract(raw, '$.bans.EconomyBan')) VIRTUAL
);
"""

# Separado da tabela de propósito: num banco já existente o CREATE TABLE acima é
# pulado, e um índice sobre coluna nova falharia com "no such column" se rodasse
# antes do ALTER TABLE que a adiciona. A ordem correta é tabela → colunas → índices.
SCHEMA_INDICES = """
CREATE INDEX IF NOT EXISTS idx_perfil_nome    ON perfil(persona_name);
CREATE INDEX IF NOT EXISTS idx_perfil_vanity  ON perfil(vanity);
CREATE INDEX IF NOT EXISTS idx_perfil_pais    ON perfil(country);
CREATE INDEX IF NOT EXISTS idx_perfil_vac     ON perfil(vac_banned);
CREATE INDEX IF NOT EXISTS idx_perfil_criada  ON perfil(created_at);
CREATE INDEX IF NOT EXISTS idx_perfil_fetched ON perfil(fetched_at);
CREATE INDEX IF NOT EXISTS idx_perfil_avatar  ON perfil(avatar_hash);
"""

# Fonte única das colunas indexadas textualmente. O DDL abaixo é gerado a partir
# desta tupla justamente para não existir a possibilidade de adicionar uma coluna
# ao índice e esquecer de atualizar uma das três triggers.
FTS_COLS = ("persona_name", "real_name", "vanity")

_LISTA = ", ".join(FTS_COLS)
_NOVOS = ", ".join(f"new.{c}" for c in FTS_COLS)
_ANTIGOS = ", ".join(f"old.{c}" for c in FTS_COLS)

# content='perfil': o índice textual lê da tabela base, sem guardar 2ª cópia dos
# valores. 'rebuild' repovoa o índice a partir dela.
FTS_RECREATE = f"""
DROP TRIGGER IF EXISTS perfil_ai;
DROP TRIGGER IF EXISTS perfil_ad;
DROP TRIGGER IF EXISTS perfil_au;
DROP TABLE IF EXISTS perfil_fts;

CREATE VIRTUAL TABLE perfil_fts USING fts5(
  {_LISTA}, content='perfil', content_rowid='rowid'
);

CREATE TRIGGER perfil_ai AFTER INSERT ON perfil BEGIN
  INSERT INTO perfil_fts(rowid, {_LISTA}) VALUES (new.rowid, {_NOVOS});
END;

CREATE TRIGGER perfil_ad AFTER DELETE ON perfil BEGIN
  INSERT INTO perfil_fts(perfil_fts, rowid, {_LISTA})
    VALUES ('delete', old.rowid, {_ANTIGOS});
END;

CREATE TRIGGER perfil_au AFTER UPDATE ON perfil BEGIN
  INSERT INTO perfil_fts(perfil_fts, rowid, {_LISTA})
    VALUES ('delete', old.rowid, {_ANTIGOS});
  INSERT INTO perfil_fts(rowid, {_LISTA}) VALUES (new.rowid, {_NOVOS});
END;

INSERT INTO perfil_fts(perfil_fts) VALUES ('rebuild');
"""

CAMPOS = (
    "steamid64", "account_id", "persona_name", "real_name", "vanity",
    "avatar_hash", "country", "profile_url", "visibility", "persona_state",
    "created_at", "last_logoff", "clan_id", "vac_banned", "vac_ban_count",
    "game_ban_count", "community_banned", "economy_ban", "fetched_at",
    "first_seen_at",
)

CAMPOS_PUBLICOS = ", ".join(CAMPOS)

# Versão qualificada: no JOIN com perfil_fts os nomes das colunas indexadas
# existem nas duas tabelas, e o SQLite recusa a coluna ambígua.
CAMPOS_PUBLICOS_P = ", ".join(f"p.{c}" for c in CAMPOS)


def documento_canonico(summary: dict | None, bans: dict | None) -> str:
    """JSON estável: chaves ordenadas e sem espaços.

    Forma canônica torna o documento comparável byte a byte, o que é o que
    permitiria detectar "nada mudou" se histórico entrar depois.
    """
    return json.dumps(
        {"summary": summary, "bans": bans},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def _consulta_fts(termo: str) -> str:
    """Transforma texto livre numa expressão FTS5 de prefixo, à prova de sintaxe.

    O termo do usuário nunca vira sintaxe de consulta: cada palavra é envolvida em
    aspas duplas, e as aspas presentes no texto são dobradas — dentro de uma
    string literal do FTS5, `""` é uma aspa literal. Sem isso, um `"` no meio do
    termo fecharia a string e o resto seria interpretado como operadores
    (`OR`, `NEAR`, `coluna:`), resultando em erro de sintaxe.
    """
    palavras = [p for p in termo.split() if p]
    if not palavras:
        return ""
    return " ".join(f'"{p.replace(chr(34), chr(34) * 2)}"*' for p in palavras)


class ProfileStore:
    """Acesso ao banco. Abre uma conexão por operação — barato no SQLite e evita
    compartilhar conexão entre as threads do `asyncio.to_thread`."""

    def __init__(self, path: Path) -> None:
        self._path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as con:
            self._migrar(con)

    @property
    def path(self) -> Path:
        return self._path

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self._path, timeout=10.0)
        con.row_factory = sqlite3.Row
        # WAL: leitura não bloqueia escrita. busy_timeout evita "database is
        # locked" quando duas requisições gravam ao mesmo tempo.
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA busy_timeout=10000")
        con.execute("PRAGMA synchronous=NORMAL")
        return con

    @staticmethod
    def _migrar(con: sqlite3.Connection) -> None:
        """Leva o banco ao schema atual, seja ele novo ou de uma versão anterior.

        Idempotente: roda a cada inicialização. Bancos criados antes de uma coluna
        existir a ganham por ALTER TABLE — o SQLite aceita adicionar coluna gerada
        VIRTUAL, que não reescreve as linhas existentes.
        """
        con.executescript(SCHEMA_PERFIL)

        # table_xinfo, não table_info: `table_info` **omite** colunas geradas, então
        # usá-lo aqui faria a migração tentar adicionar `vanity` a cada
        # inicialização e falhar com "duplicate column name".
        colunas = {linha["name"] for linha in con.execute("PRAGMA table_xinfo(perfil)")}
        for nome, expressao in (("vanity", VANITY_EXPR),):
            if nome not in colunas:
                con.execute(
                    f"ALTER TABLE perfil ADD COLUMN {nome} TEXT "
                    f"GENERATED ALWAYS AS ({expressao}) VIRTUAL"
                )

        # Só agora: os índices podem referenciar colunas acrescentadas acima.
        con.executescript(SCHEMA_INDICES)

        # Se o conjunto de colunas do índice textual divergir do desejado, recria e
        # repovoa. Cobre também o banco novo, em que perfil_fts ainda não existe.
        fts_atual = tuple(
            linha["name"] for linha in con.execute("PRAGMA table_info(perfil_fts)")
        )
        if fts_atual != FTS_COLS:
            con.executescript(FTS_RECREATE)

    # ---- implementações síncronas ----

    def _save(self, steamid64: str, doc: str, agora: int) -> dict:
        with self._connect() as con:
            con.execute(
                """
                INSERT INTO perfil (steamid64, raw, fetched_at, first_seen_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(steamid64) DO UPDATE SET
                  raw = excluded.raw,
                  fetched_at = excluded.fetched_at
                """,
                (steamid64, doc, agora, agora),
            )
            linha = con.execute(
                "SELECT fetched_at, first_seen_at FROM perfil WHERE steamid64 = ?",
                (steamid64,),
            ).fetchone()
        return {
            "fetched_at": linha["fetched_at"],
            "first_seen_at": linha["first_seen_at"],
            "novo": linha["fetched_at"] == linha["first_seen_at"],
        }

    def _get(self, steamid64: str) -> dict | None:
        with self._connect() as con:
            linha = con.execute(
                f"SELECT {CAMPOS_PUBLICOS} FROM perfil WHERE steamid64 = ?", (steamid64,)
            ).fetchone()
        return dict(linha) if linha else None

    def _search(self, termo: str, limite: int, offset: int) -> dict:
        """Busca por nome parecido na base local — o que a Web API não oferece.

        Cobre persona name, nome real e vanity URL.
        """
        termo = termo.strip()
        if not termo:
            # Sem termo: os mais recentes primeiro. Serve para inspecionar o que
            # já foi salvo sem precisar adivinhar um nome.
            with self._connect() as con:
                total = con.execute("SELECT COUNT(*) FROM perfil").fetchone()[0]
                linhas = con.execute(
                    f"SELECT {CAMPOS_PUBLICOS} FROM perfil "
                    "ORDER BY fetched_at DESC LIMIT ? OFFSET ?",
                    (limite, offset),
                ).fetchall()
            return {"total": total, "itens": [dict(x) for x in linhas]}

        consulta = _consulta_fts(termo)
        if not consulta:
            return {"total": 0, "itens": []}
        try:
            with self._connect() as con:
                total = con.execute(
                    "SELECT COUNT(*) FROM perfil_fts WHERE perfil_fts MATCH ?",
                    (consulta,),
                ).fetchone()[0]
                linhas = con.execute(
                    f"""
                    SELECT {CAMPOS_PUBLICOS_P}
                    FROM perfil_fts f
                    JOIN perfil p ON p.rowid = f.rowid
                    WHERE perfil_fts MATCH ?
                    ORDER BY f.rank
                    LIMIT ? OFFSET ?
                    """,
                    (consulta, limite, offset),
                ).fetchall()
        except sqlite3.OperationalError:
            # Rede de segurança: qualquer termo que ainda produza sintaxe inválida
            # no FTS5 vira "nenhum resultado", não erro 500.
            return {"total": 0, "itens": []}
        return {"total": total, "itens": [dict(x) for x in linhas]}

    def _stats(self) -> dict:
        try:
            with self._connect() as con:
                perfis = con.execute("SELECT COUNT(*) FROM perfil").fetchone()[0]
        except sqlite3.Error:
            return {"profiles": 0, "bytes": 0}
        bytes_ = sum(
            p.stat().st_size
            for p in self._path.parent.glob(f"{self._path.name}*")
            if p.is_file()
        )
        return {"profiles": perfis, "bytes": bytes_}

    # ---- fachada assíncrona: sqlite3 é bloqueante, então sai do event loop ----

    async def save(self, steamid64: str, doc: str, agora: int) -> dict:
        return await asyncio.to_thread(self._save, steamid64, doc, agora)

    async def get(self, steamid64: str) -> dict | None:
        return await asyncio.to_thread(self._get, steamid64)

    async def search(self, termo: str, limite: int = 25, offset: int = 0) -> dict:
        return await asyncio.to_thread(self._search, termo, limite, offset)

    async def stats(self) -> dict:
        return await asyncio.to_thread(self._stats)


__all__ = ["ProfileStore", "documento_canonico"]
