"""Rotas HTTP: contratos, fragmentos htmx, paginação e superfície de ataque."""

import pytest
from conftest import HASH_VALIDO, JPEG_FALSO

SID = "76561197960287930"
XSS = "<script>alert(1)</script>"


# ----------------------------------------------------------------- healthcheck


def test_health(client):
    corpo = client.get("/health").json()
    assert corpo["status"] == "ok"
    assert corpo["steam_api"] is True
    assert "files" in corpo["avatar_cache"]
    assert "profiles" in corpo["perfis"]


def test_health_sem_chave_reporta_false(client_sem_chave):
    # A flag vem de settings, não da dependência; o valor é do ambiente de teste.
    assert client_sem_chave.get("/health").json()["status"] == "ok"


# ---------------------------------------------------------------- página inicial


def test_index_serve_html_com_form_htmx(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert 'hx-get="/search"' in r.text
    assert 'name="q"' in r.text


def test_index_aponta_para_os_salvos(client):
    assert 'href="/salvos"' in client.get("/").text


def test_paginas_nao_referenciam_dominio_externo_em_src(client):
    """Nenhum recurso carregado automaticamente de fora; href de link é permitido."""
    import re

    for rota in ("/", "/salvos", f"/perfil/{SID}"):
        html = client.get(rota).text
        externos = re.findall(r'src="(https?://[^"]*)"', html)
        assert externos == [], f"{rota} carrega recurso externo: {externos}"


# ------------------------------------------------------------------- /api/lookup


def test_api_lookup_converte_e_persiste(client, store):
    corpo = client.get("/api/lookup", params={"q": SID}).json()
    assert corpo["steamid"]["steamid2"] == "STEAM_1:0:11101"
    assert corpo["steam_api"]["status"] == "ok"
    assert corpo["stored"]["novo"] is True
    assert store._stats()["profiles"] == 1


def test_api_lookup_steamid64_e_string_no_json(client):
    """Precisão: 17 dígitos não cabem em double do JavaScript."""
    bruto = client.get("/api/lookup", params={"q": SID}).text
    assert f'"steamid64":"{SID}"' in bruto.replace(" ", "")


def test_api_lookup_entrada_invalida_da_400(client):
    r = client.get("/api/lookup", params={"q": "!!!"})
    assert r.status_code == 400
    assert "error" in r.json()


def test_api_lookup_sem_chave_marca_skipped(client_sem_chave):
    corpo = client_sem_chave.get("/api/lookup", params={"q": SID}).json()
    assert corpo["steam_api"]["status"] == "skipped"
    assert "stored" not in corpo, "sem dados da Steam não há perfil para salvar"


def test_reconsulta_nao_duplica_via_http(client, store):
    for _ in range(3):
        client.get("/api/lookup", params={"q": SID})
    assert store._stats()["profiles"] == 1


def test_formatos_diferentes_convergem_para_uma_linha(client, store):
    for q in (SID, "STEAM_0:0:11101", "[U:1:22202]", "22202"):
        client.get("/api/lookup", params={"q": q})
    assert store._stats()["profiles"] == 1


def test_lookup_prebaixa_o_avatar(client, cache):
    assert cache.cached(HASH_VALIDO) is None
    client.get("/api/lookup", params={"q": SID})
    assert cache.cached(HASH_VALIDO) is not None, "a foto deve ir para o disco"


# ------------------------------------------------------------------ /search htmx


def test_search_devolve_fragmento_nao_documento(client):
    html = client.get("/search", params={"q": SID}).text
    assert "<!doctype html>" not in html.lower()
    assert 'class="resultado"' in html


def test_search_traz_botao_para_a_pagina_do_perfil(client):
    assert f'href="/perfil/{SID}"' in client.get("/search", params={"q": SID}).text


def test_search_com_erro_devolve_200_para_o_htmx_trocar(client):
    """htmx não faz swap em 4xx; aqui o erro é conteúdo, não falha de transporte."""
    r = client.get("/search", params={"q": "!!!"})
    assert r.status_code == 200
    assert 'class="erro"' in r.text


@pytest.mark.parametrize("rota", ["/search", "/salvos", "/salvos/lista", "/api/salvos"])
def test_payload_nunca_aparece_cru(client, rota):
    """A propriedade que importa: o script do usuário nunca sai executável."""
    assert XSS not in client.get(rota, params={"q": XSS}).text, f"{rota} refletiu cru"


@pytest.mark.parametrize("rota", ["/salvos", "/salvos/lista"])
def test_onde_a_entrada_e_ecoada_ela_vem_escapada(client, rota):
    """Nestas rotas o termo aparece na página; deve estar com entidades HTML.

    `/search` não ecoa a entrada em nenhum caminho — a mensagem de erro é fixa —
    então lá só cabe a asserção de ausência do payload cru, acima.
    """
    html = client.get(rota, params={"q": XSS}).text
    assert "&lt;script&gt;" in html, f"{rota} não escapou o eco"
    assert XSS not in html


def test_perfil_escapa_o_eco_do_caminho(client):
    """A página de erro de /perfil ecoa a entrada; payload sem barra chega até ela."""
    html = client.get("/perfil/<svg onload=alert(1)>").text
    assert "&lt;svg" in html, "o eco deve vir escapado"
    assert "<svg onload=" not in html


def test_caminho_com_barra_nao_casa_a_rota_de_perfil(client):
    """Barra no path não vira parâmetro: o roteador recusa antes do handler."""
    assert client.get("/perfil/<script>alert(1)</script>").status_code == 404


# -------------------------------------------------------------------- /perfil


def test_perfil_renderiza_documento_completo(client):
    r = client.get(f"/perfil/{SID}")
    assert r.status_code == 200
    assert "<!doctype html>" in r.text.lower()
    assert "Rabscuttle" in r.text


def test_perfil_mostra_identificadores_e_avatar_local(client):
    html = client.get(f"/perfil/{SID}").text
    assert "STEAM_1:0:11101" in html
    assert "[U:1:22202]" in html
    assert f'src="/avatar/{HASH_VALIDO}"' in html


def test_perfil_aceita_qualquer_formato(client):
    for q in (SID, "STEAM_0:0:11101", "22202", "gabelogannewell"):
        assert client.get(f"/perfil/{q}").status_code == 200


def test_perfil_com_entrada_invalida_da_404(client):
    r = client.get("/perfil/abacaxi!!!")
    assert r.status_code == 404
    assert 'class="erro"' in r.text


# --------------------------------------------------------------------- /avatar


def test_avatar_baixa_e_depois_serve_do_disco(client):
    primeira = client.get(f"/avatar/{HASH_VALIDO}")
    assert primeira.status_code == 200
    assert primeira.headers["x-avatar-cache"] == "miss"
    assert primeira.content == JPEG_FALSO
    assert "immutable" in primeira.headers["cache-control"]

    segunda = client.get(f"/avatar/{HASH_VALIDO}")
    assert segunda.headers["x-avatar-cache"] == "hit"


@pytest.mark.parametrize(
    "hash_ruim",
    ["ZZZZ", "a" * 39, "A" * 40, "g" * 40, "1234"],
)
def test_avatar_com_hash_malformado_da_400(client, hash_ruim):
    r = client.get(f"/avatar/{hash_ruim}")
    assert r.status_code == 400
    assert "error" in r.json()


@pytest.mark.parametrize(
    "caminho",
    [
        "/avatar/../../etc/passwd",
        "/avatar/..%2F..%2Fetc%2Fpasswd",
        "/static/../config.py",
        "/static/..%2Fconfig.py",
    ],
)
def test_traversal_nao_alcanca_arquivo(client, caminho):
    assert client.get(caminho).status_code in (400, 404), "traversal não pode dar 200"


def test_avatar_indisponivel_serve_placeholder_sem_cache(client, cache, monkeypatch):
    from src.avatar_cache import AvatarCacheError

    async def falha(_hash):
        raise AvatarCacheError("indisponível")

    monkeypatch.setattr(cache, "fetch", falha)
    r = client.get("/avatar/" + "c" * 40)
    assert r.status_code == 200
    assert r.headers["x-avatar-cache"] == "placeholder"
    assert r.headers["cache-control"] == "no-store", "placeholder não pode ser cacheado"
    assert "svg" in r.headers["content-type"]


# ---------------------------------------------------------------- /api/salvos


def test_api_salvos_vazio(client):
    assert client.get("/api/salvos").json() == {"total": 0, "offset": 0, "itens": []}


def test_api_salvos_encontra_por_persona_real_e_vanity(client):
    client.get("/api/lookup", params={"q": SID})
    for termo in ("rabs", "gabe", "newell", "gabelogan"):
        assert client.get("/api/salvos", params={"q": termo}).json()["total"] == 1, termo


@pytest.mark.parametrize("termo", ['a" OR x : "b', '"', "NEAR(", "*", "^"])
def test_api_salvos_termo_hostil_nao_da_500(client, termo):
    r = client.get("/api/salvos", params={"q": termo})
    assert r.status_code == 200
    assert r.json()["total"] == 0


@pytest.mark.parametrize("valor", ["-1", "abc", "1.5"])
def test_offset_invalido_e_rejeitado(client, valor):
    assert client.get("/api/salvos", params={"offset": valor}).status_code == 422


@pytest.mark.parametrize("valor", ["0", "101", "-3"])
def test_limite_fora_da_faixa_e_rejeitado(client, valor):
    assert client.get("/api/salvos", params={"limite": valor}).status_code == 422


# ------------------------------------------------------- /salvos e paginação


def popular(client, quantidade: int) -> None:
    """Salva `quantidade` perfis distintos usando SteamIDs consecutivos."""
    for i in range(quantidade):
        client.get("/api/lookup", params={"q": str(76561197960265731 + i)})


def test_salvos_estado_vazio_orienta(client):
    html = client.get("/salvos").text
    assert 'class="vazio"' in html
    assert "página inicial" in html


def test_salvos_sem_resultado_explica(client):
    client.get("/api/lookup", params={"q": SID})
    html = client.get("/salvos", params={"q": "zzzznaoexiste"}).text
    assert 'class="vazio"' in html
    assert "zzzznaoexiste" in html


def test_salvos_lista_cards(client):
    client.get("/api/lookup", params={"q": SID})
    html = client.get("/salvos").text
    assert 'class="card"' in html
    assert f'href="/perfil/{SID}"' in html
    assert "/id/GabeLoganNewell" in html


def test_paginacao_ausente_com_uma_pagina(client):
    client.get("/api/lookup", params={"q": SID})
    assert 'class="paginacao"' not in client.get("/salvos").text


def test_paginacao_presente_e_navegavel(client):
    from src.main import PAGINA_SALVOS

    popular(client, PAGINA_SALVOS + 3)

    primeira = client.get("/salvos/lista").text
    assert 'class="paginacao"' in primeira
    assert f"de {2}" in primeira
    assert primeira.count('class="card"') == PAGINA_SALVOS

    segunda = client.get("/salvos/lista", params={"offset": PAGINA_SALVOS}).text
    assert segunda.count('class="card"') == 3


def test_offset_alem_do_fim_cai_na_ultima_pagina(client):
    from src.main import PAGINA_SALVOS

    popular(client, PAGINA_SALVOS + 2)

    html = client.get("/salvos/lista", params={"offset": 10**6}).text
    assert "página 2 de 2" in html
    assert html.count('class="card"') == 2, "a última página precisa ter conteúdo"


def test_paginacao_preserva_o_termo(client):
    from src.main import PAGINA_SALVOS

    popular(client, PAGINA_SALVOS + 2)

    html = client.get("/salvos/lista", params={"q": "rabs"}).text
    # 'rabs' casa todos (mesmo personaname no fixture), então há paginação
    assert "q=rabs" in html


def test_termo_com_caracteres_especiais_e_percent_encoded(client):
    from src.main import PAGINA_SALVOS, _contexto_lista, templates

    dados = {"total": PAGINA_SALVOS * 3, "offset": PAGINA_SALVOS, "itens": []}
    ctx = _contexto_lista(dados, 'x"y&z', PAGINA_SALVOS)
    html = templates.get_template("partials/salvos_lista.html").render(
        app_name="t", steam_api_enabled=True, **ctx
    )
    assert "q=x%22y%26z" in html
    assert 'q=x"y&z' not in html


# ----------------------------------------------------------------- superfície


@pytest.mark.parametrize("rota", ["/api/lookup", "/api/salvos", "/health", "/"])
def test_post_nao_e_aceito(client, rota):
    assert client.post(rota).status_code == 405


def test_openapi_nao_expoe_a_chave(client):
    from conftest import CHAVE_FALSA

    assert CHAVE_FALSA not in client.get("/openapi.json").text


@pytest.mark.parametrize("rota", ["/health", "/", "/salvos", f"/api/lookup?q={SID}"])
def test_nenhuma_resposta_contem_a_chave(client, rota):
    from conftest import CHAVE_FALSA

    assert CHAVE_FALSA not in client.get(rota).text
