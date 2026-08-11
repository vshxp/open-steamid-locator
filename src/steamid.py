"""Parsing e conversão de SteamID. Aritmética pura — não toca a rede.

A conversão entre formatos não precisa de API key: SteamID64 é apenas
`base + account_id`, e os campos universo/tipo/instância são fatias de bits do
mesmo inteiro de 64 bits.
"""

import re
from dataclasses import dataclass
from urllib.parse import urlparse

# 0x0110000100000000 — universo 1 (público), tipo 1 (individual), instância 1
STEAM64_BASE = 76561197960265728

ACCOUNT_ID_MAX = 0xFFFFFFFF

UNIVERSES = {
    0: "individual/inválido",
    1: "público",
    2: "beta",
    3: "interno",
    4: "desenvolvimento",
    5: "RC",
}

ACCOUNT_TYPES = {
    0: "inválido",
    1: "individual",
    2: "multisseat",
    3: "game server",
    4: "game server anônimo",
    5: "pending",
    6: "content server",
    7: "clã/grupo",
    8: "chat",
    9: "console P2P",
    10: "usuário anônimo",
}

_RE_STEAM2 = re.compile(r"^STEAM_([0-5]):([01]):(\d+)$", re.IGNORECASE)
_RE_STEAM3 = re.compile(r"^\[?U:([0-5]):(\d+)(?::\d+)?\]?$", re.IGNORECASE)
_RE_DIGITS = re.compile(r"^\d+$")
_RE_VANITY = re.compile(r"^[A-Za-z0-9_.-]{2,32}$")

_RE_URL_PROFILE = re.compile(r"/profiles/(\d{1,20})")
_RE_URL_VANITY = re.compile(r"/id/([A-Za-z0-9_.-]{2,32})")


class SteamIdError(ValueError):
    """Entrada que não corresponde a nenhum formato reconhecido de SteamID."""


@dataclass(frozen=True)
class ParsedQuery:
    """Resultado do parsing: ou já temos um SteamID64, ou temos um vanity a resolver."""

    raw: str
    detected: str
    steamid64: int | None = None
    vanity: str | None = None


def parse_query(raw: str) -> ParsedQuery:
    """Reconhece SteamID64, STEAM_x:y:z, [U:1:z], account ID, URL de perfil ou vanity.

    Levanta SteamIdError se nada casar ou se o valor estiver fora de faixa.
    """
    text = (raw or "").strip()
    if not text:
        raise SteamIdError("Informe um SteamID ou uma URL de perfil Steam.")

    # URLs primeiro: o caminho carrega a informação, o resto é ruído.
    if "/" in text or text.lower().startswith(("http://", "https://")):
        candidate = text if "://" in text else f"https://{text}"
        path = urlparse(candidate).path

        if match := _RE_URL_PROFILE.search(path):
            return ParsedQuery(
                raw=text,
                detected="url_perfil",
                steamid64=_validate_steam64(int(match.group(1)), origin="URL de perfil"),
            )

        if match := _RE_URL_VANITY.search(path):
            return ParsedQuery(raw=text, detected="url_vanity", vanity=match.group(1))

        raise SteamIdError("URL não reconhecida. Use /profiles/<steamid64> ou /id/<vanity>.")

    if match := _RE_STEAM2.match(text):
        universe, parity, half = (int(g) for g in match.groups())
        account_id = half * 2 + parity
        if not 1 <= account_id <= ACCOUNT_ID_MAX:
            raise SteamIdError(f"Account ID fora de faixa em SteamID2: {account_id}")
        # Universo 0 aparece em SteamID2 legado; trata-se como público (1).
        del universe
        return ParsedQuery(raw=text, detected="steamid2", steamid64=STEAM64_BASE + account_id)

    if match := _RE_STEAM3.match(text):
        _, account_id_str = match.groups()
        account_id = int(account_id_str)
        if not 1 <= account_id <= ACCOUNT_ID_MAX:
            raise SteamIdError(f"Account ID fora de faixa em SteamID3: {account_id}")
        return ParsedQuery(raw=text, detected="steamid3", steamid64=STEAM64_BASE + account_id)

    if _RE_DIGITS.match(text):
        value = int(text)
        if value > ACCOUNT_ID_MAX:
            return ParsedQuery(
                raw=text,
                detected="steamid64",
                steamid64=_validate_steam64(value, origin="SteamID64"),
            )
        if value >= 1:
            return ParsedQuery(raw=text, detected="account_id", steamid64=STEAM64_BASE + value)
        raise SteamIdError("Account ID deve ser maior que zero.")

    if _RE_VANITY.match(text):
        return ParsedQuery(raw=text, detected="vanity", vanity=text)

    raise SteamIdError(
        "Formato não reconhecido. Aceito: SteamID64, STEAM_0:1:123, [U:1:246], "
        "account ID, URL de perfil ou vanity."
    )


def _validate_steam64(value: int, *, origin: str) -> int:
    account_id = value & ACCOUNT_ID_MAX
    if value < STEAM64_BASE or account_id == 0:
        raise SteamIdError(f"{origin} inválido: {value} não corresponde a uma conta individual.")
    return value


@dataclass(frozen=True)
class SteamIdentity:
    """Um SteamID expresso em todos os formatos, mais a decomposição de bits."""

    # String, não int: JSON em JavaScript perde precisão acima de 2^53 e o
    # SteamID64 tem 17 dígitos. Enviar como número corromperia o valor no browser.
    steamid64: str
    steamid2: str
    steamid2_legacy: str
    steamid3: str
    account_id: int
    universe: int
    universe_name: str
    account_type: int
    account_type_name: str
    instance: int
    profile_url: str

    def as_dict(self) -> dict:
        return {
            "steamid64": self.steamid64,
            "steamid2": self.steamid2,
            "steamid2_legacy": self.steamid2_legacy,
            "steamid3": self.steamid3,
            "account_id": self.account_id,
            "universe": {"value": self.universe, "name": self.universe_name},
            "account_type": {"value": self.account_type, "name": self.account_type_name},
            "instance": self.instance,
            "profile_url": self.profile_url,
        }


def to_identity(steamid64: int) -> SteamIdentity:
    """Decompõe um SteamID64 em todos os formatos equivalentes."""
    account_id = steamid64 & ACCOUNT_ID_MAX
    instance = (steamid64 >> 32) & 0xFFFFF
    account_type = (steamid64 >> 52) & 0xF
    universe = (steamid64 >> 56) & 0xFF

    half, parity = divmod(account_id, 2)

    return SteamIdentity(
        steamid64=str(steamid64),
        steamid2=f"STEAM_{universe}:{parity}:{half}",
        steamid2_legacy=f"STEAM_0:{parity}:{half}",
        steamid3=f"[U:1:{account_id}]",
        account_id=account_id,
        universe=universe,
        universe_name=UNIVERSES.get(universe, "desconhecido"),
        account_type=account_type,
        account_type_name=ACCOUNT_TYPES.get(account_type, "desconhecido"),
        instance=instance,
        profile_url=f"https://steamcommunity.com/profiles/{steamid64}",
    )
