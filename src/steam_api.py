"""Cliente mínimo da Steam Web API.

Cobre só o necessário para a busca por SteamID: resolver vanity URL e trazer
resumo do perfil + bans. Tudo o que falha aqui falha de forma explícita — a API
da Steam devolve 200 com corpo vazio para perfis privados, então "não vi" e
"não existe" precisam ser distinguíveis pelo chamador.
"""

import httpx

BASE_URL = "https://api.steampowered.com"

# 42 = "no match" em ResolveVanityURL; não é erro, é ausência.
VANITY_NO_MATCH = 42


class SteamApiError(RuntimeError):
    """Falha ao falar com a Steam Web API, com motivo legível."""


class SteamClient:
    def __init__(self, api_key: str, http: httpx.AsyncClient) -> None:
        self._api_key = api_key
        self._http = http

    async def _get(self, path: str, params: dict) -> dict:
        try:
            response = await self._http.get(
                f"{BASE_URL}{path}", params={"key": self._api_key, **params}
            )
        except httpx.TimeoutException as exc:
            raise SteamApiError("Timeout ao consultar a Steam Web API.") from exc
        except httpx.RequestError as exc:
            # Só o nome da classe, nunca o texto da exceção: a URL desta
            # requisição carrega a API key, e algumas exceções do httpx incluem a
            # URL na mensagem. Interpolar `exc` aqui colocaria a chave no corpo da
            # resposta HTTP. O detalhe completo segue em __cause__, para o log.
            raise SteamApiError(
                f"Falha de rede ao consultar a Steam ({type(exc).__name__})."
            ) from exc

        if response.status_code in (401, 403):
            raise SteamApiError("Steam recusou a chave (401/403). Verifique STEAM_API_KEY no .env.")
        if response.status_code == 429:
            raise SteamApiError(
                "Rate limit da Steam atingido (429). Aguarde antes de tentar de novo."
            )
        if response.status_code >= 500:
            raise SteamApiError(f"Steam indisponível (HTTP {response.status_code}).")
        if response.status_code != 200:
            raise SteamApiError(f"Resposta inesperada da Steam: HTTP {response.status_code}.")

        try:
            return response.json()
        except ValueError as exc:
            raise SteamApiError("Steam devolveu corpo não-JSON.") from exc

    async def resolve_vanity(self, vanity: str) -> int | None:
        """vanity URL → SteamID64. Devolve None quando o vanity não existe."""
        payload = await self._get("/ISteamUser/ResolveVanityURL/v1/", {"vanityurl": vanity})
        result = payload.get("response", {})
        if result.get("success") == VANITY_NO_MATCH:
            return None
        steamid = result.get("steamid")
        if not steamid:
            raise SteamApiError(
                f"ResolveVanityURL respondeu sem steamid (success={result.get('success')})."
            )
        return int(steamid)

    async def player_summary(self, steamid64: int) -> dict | None:
        """Resumo do perfil. None quando a Steam não devolve o jogador."""
        payload = await self._get(
            "/ISteamUser/GetPlayerSummaries/v2/", {"steamids": str(steamid64)}
        )
        players = payload.get("response", {}).get("players") or []
        return players[0] if players else None

    async def player_bans(self, steamid64: int) -> dict | None:
        payload = await self._get("/ISteamUser/GetPlayerBans/v1/", {"steamids": str(steamid64)})
        players = payload.get("players") or []
        return players[0] if players else None
