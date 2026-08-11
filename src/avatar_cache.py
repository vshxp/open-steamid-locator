"""Cache local de avatares da Steam.

O `avatarhash` que a API devolve é a chave de cache ideal: ele muda quando o
usuário troca de avatar, então o conteúdo apontado por um hash é imutável. Isso
permite baixar uma vez, guardar em disco e servir para sempre sem revalidar.

Efeito colateral desejado: a página de perfil deixa de fazer requisição a
domínio externo. O browser só fala com este servidor.
"""

import re
import tempfile
from pathlib import Path

import httpx

# 40 hex, formato do avatarhash. Validação estrita: o hash entra na URL da CDN e
# no nome do arquivo em disco, então qualquer coisa fora disso é rejeitada antes
# de tocar rede ou filesystem (evita path traversal e SSRF).
AVATAR_HASH_RE = re.compile(r"^[0-9a-f]{40}$")

CDN_TEMPLATE = "https://avatars.steamstatic.com/{hash}_full.jpg"

# Avatar full da Steam tem ~30 KB. 2 MB é folga generosa e ainda protege contra
# gravar em disco algo que não deveria estar ali.
MAX_BYTES = 2 * 1024 * 1024

JPEG_MAGIC = b"\xff\xd8\xff"


class AvatarCacheError(RuntimeError):
    """Não foi possível obter o avatar. O chamador serve um placeholder."""


class AvatarCache:
    def __init__(self, directory: Path, http: httpx.AsyncClient) -> None:
        self._dir = directory
        self._http = http
        self._dir.mkdir(parents=True, exist_ok=True)

    @property
    def directory(self) -> Path:
        return self._dir

    def _path_for(self, avatar_hash: str) -> Path:
        return self._dir / f"{avatar_hash}_full.jpg"

    def cached(self, avatar_hash: str) -> Path | None:
        """Caminho local se já estiver em cache, senão None."""
        path = self._path_for(avatar_hash)
        try:
            if path.is_file() and path.stat().st_size > 0:
                return path
        except OSError:
            return None
        return None

    async def fetch(self, avatar_hash: str) -> Path:
        """Baixa da CDN da Steam e grava em disco. Devolve o caminho local."""
        url = CDN_TEMPLATE.format(hash=avatar_hash)

        try:
            response = await self._http.get(url, follow_redirects=True)
        except httpx.TimeoutException as exc:
            raise AvatarCacheError(f"Timeout ao baixar avatar {avatar_hash}.") from exc
        except httpx.RequestError as exc:
            raise AvatarCacheError(f"Falha de rede ao baixar avatar: {exc}") from exc

        if response.status_code != 200:
            raise AvatarCacheError(
                f"CDN da Steam respondeu HTTP {response.status_code} para {avatar_hash}."
            )

        data = response.content

        if len(data) > MAX_BYTES:
            raise AvatarCacheError(
                f"Avatar {avatar_hash} tem {len(data)} bytes, acima do limite."
            )
        if not data.startswith(JPEG_MAGIC):
            # Página de erro disfarçada de imagem não vira arquivo em cache.
            raise AvatarCacheError(f"Conteúdo de {avatar_hash} não é JPEG.")

        return self._write_atomic(avatar_hash, data)

    def _write_atomic(self, avatar_hash: str, data: bytes) -> Path:
        """Grava via arquivo temporário + rename.

        Escrita direta no destino final deixaria uma janela em que outra
        requisição serviria um JPEG truncado. `os.replace` é atômico no mesmo
        filesystem, então o arquivo só existe completo.
        """
        destino = self._path_for(avatar_hash)
        try:
            with tempfile.NamedTemporaryFile(
                dir=self._dir, prefix=f".{avatar_hash}.", suffix=".tmp", delete=False
            ) as tmp:
                tmp.write(data)
                tmp.flush()
                temporario = Path(tmp.name)
            # NamedTemporaryFile cria com 0600. Um cache servido não precisa ser
            # secreto, e 0600 impediria outro usuário (ex.: container prod como
            # `app`) de ler arquivos escritos antes como root.
            temporario.chmod(0o644)
            temporario.replace(destino)
        except OSError as exc:
            raise AvatarCacheError(f"Falha ao gravar avatar em cache: {exc}") from exc
        return destino

    def stats(self) -> dict:
        """Contagem e tamanho do cache — para /health e inspeção."""
        try:
            arquivos = [p for p in self._dir.glob("*_full.jpg") if p.is_file()]
        except OSError:
            return {"files": 0, "bytes": 0}
        return {
            "files": len(arquivos),
            "bytes": sum(p.stat().st_size for p in arquivos),
        }
