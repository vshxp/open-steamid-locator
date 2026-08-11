from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent

# Fora de src/ de propósito: src/ é montado por volume a partir do host em
# desenvolvimento, e cache não deve poluir o repositório. Como o WORKDIR do
# container é /app e o código vive em /app/src, este caminho resolve para
# /app/cache no container e <repo>/cache fora dele.
DEFAULT_CACHE_DIR = BASE_DIR.parent / "cache"

# Separado do cache de propósito: cache é descartável, isto é dado. Compartilhar o
# diretório convidaria alguém a apagar os perfis salvos junto com os avatares.
DEFAULT_DATA_DIR = BASE_DIR.parent / "data"


class Settings(BaseSettings):
    """Configuração da aplicação, lida do .env único compartilhado com o Docker."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "open-steamid-locator"
    # Dentro do container é o único bind que o host alcança; a exposição real é
    # controlada pelo mapeamento de portas do Docker, não por este valor.
    host: str = "0.0.0.0"  # noqa: S104  # nosec B104
    port: int = 8000
    debug: bool = False

    # Vazia = modo offline: só conversão de SteamID, sem chamadas à Steam.
    steam_api_key: str = ""

    # Onde os avatares baixados ficam. Em Docker é um volume nomeado, para
    # sobreviver à recriação do container.
    cache_dir: Path = DEFAULT_CACHE_DIR

    # Banco dos perfis salvos. Volume próprio, separado do cache.
    data_dir: Path = DEFAULT_DATA_DIR


settings = Settings()
