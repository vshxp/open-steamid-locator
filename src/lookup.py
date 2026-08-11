"""Orquestra a busca: parsing → conversão → enriquecimento opcional via Steam API.

Fonte única de verdade do JSON de resposta. A rota HTML e a rota JSON consomem
exatamente o mesmo dicionário, então a tela nunca divergir da API.
"""

from datetime import datetime, timezone

from src.steam_api import SteamApiError, SteamClient
from src.steamid import SteamIdError, parse_query, to_identity

PERSONA_STATES = {
    0: "offline",
    1: "online",
    2: "ocupado",
    3: "ausente",
    4: "soneca",
    5: "quer negociar",
    6: "quer jogar",
}

VISIBILITY = {
    1: "privado",
    2: "só amigos",
    3: "público",
}


def _iso(timestamp: int | None) -> str | None:
    if not timestamp:
        return None
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()


def _url_segura(url: str | None) -> str | None:
    """Só devolve a URL se o esquema for http(s).

    Este valor vem da Steam e vai para um `href` no template. O autoescape do
    Jinja impede escapar do atributo, mas não impede `javascript:` ou `data:`
    de serem esquemas válidos dentro dele. O valor cru continua em `raw`.
    """
    if not url:
        return None
    return url if url.startswith(("https://", "http://")) else None


def _interpret(summary: dict | None, bans: dict | None) -> dict:
    """Traduz os códigos crus da Steam e marca o que não pôde ser visto.

    A Steam Web API é presente-do-indicativo e falha em silêncio: perfil privado
    devolve 200 com campos ausentes, não um erro. Aqui a ausência fica explícita.
    """
    interpreted: dict = {}

    if summary:
        visibility_code = summary.get("communityvisibilitystate")
        is_public = visibility_code == 3
        interpreted["visibility"] = VISIBILITY.get(visibility_code, "desconhecido")
        interpreted["persona_name"] = summary.get("personaname")
        interpreted["persona_state"] = PERSONA_STATES.get(
            summary.get("personastate"), "desconhecido"
        )
        interpreted["account_created_at"] = _iso(summary.get("timecreated"))
        interpreted["last_logoff_at"] = _iso(summary.get("lastlogoff"))
        interpreted["country"] = summary.get("loccountrycode")
        interpreted["avatar"] = summary.get("avatarfull")
        # Chave do cache local — a interface serve por /avatar/<hash>, não pela CDN.
        interpreted["avatar_hash"] = summary.get("avatarhash")
        interpreted["custom_url"] = _url_segura(summary.get("profileurl"))

        if not is_public:
            interpreted["aviso"] = (
                "Perfil não é público. Campos ausentes significam 'não pude ver', "
                "não 'não existe'."
            )

    if bans:
        vac_bans = bans.get("NumberOfVACBans", 0)
        game_bans = bans.get("NumberOfGameBans", 0)
        interpreted["bans"] = {
            "vac_banned": bans.get("VACBanned", False),
            "vac_ban_count": vac_bans,
            "game_ban_count": game_bans,
            "economy_ban": bans.get("EconomyBan"),
            "community_banned": bans.get("CommunityBanned", False),
            "days_since_last_ban": bans.get("DaysSinceLastBan")
            if (vac_bans or game_bans)
            else None,
            "clean": not (
                bans.get("VACBanned")
                or vac_bans
                or game_bans
                or bans.get("CommunityBanned")
            ),
        }

    return interpreted


async def lookup(query: str, client: SteamClient | None) -> dict:
    """Resolve a busca e devolve o dicionário completo da resposta.

    Levanta SteamIdError para entrada inválida ou vanity irresolúvel.
    """
    parsed = parse_query(query)

    if parsed.steamid64 is None:
        if client is None:
            raise SteamIdError(
                f"'{parsed.vanity}' é um vanity URL e resolvê-lo exige a Steam Web API. "
                "Configure STEAM_API_KEY no .env, ou informe um SteamID numérico."
            )
        try:
            resolved = await client.resolve_vanity(parsed.vanity or "")
        except SteamApiError as exc:
            raise SteamIdError(f"Não foi possível resolver o vanity: {exc}") from exc
        if resolved is None:
            raise SteamIdError(f"Nenhum perfil Steam com o vanity '{parsed.vanity}'.")
        steamid64 = resolved
    else:
        steamid64 = parsed.steamid64

    identity = to_identity(steamid64)

    result: dict = {
        "query": {"raw": parsed.raw, "detected": parsed.detected},
        "steamid": identity.as_dict(),
    }

    if client is None:
        result["steam_api"] = {
            "status": "skipped",
            "reason": (
                "STEAM_API_KEY não configurada — apenas a conversão de SteamID foi feita. "
                "Adicione a chave ao .env para trazer nome, avatar, país e bans."
            ),
        }
        return result

    try:
        summary = await client.player_summary(steamid64)
        bans = await client.player_bans(steamid64)
    except SteamApiError as exc:
        result["steam_api"] = {"status": "error", "reason": str(exc)}
        return result

    if summary is None and bans is None:
        result["steam_api"] = {
            "status": "not_found",
            "reason": (
                "A Steam não devolveu dados para este SteamID. O ID é válido "
                "aritmeticamente, mas pode não corresponder a uma conta existente."
            ),
        }
        return result

    result["steam_api"] = {
        "status": "ok",
        "interpreted": _interpret(summary, bans),
        "raw": {"summary": summary, "bans": bans},
    }
    return result
