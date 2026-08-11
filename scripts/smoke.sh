#!/usr/bin/env bash
# Smoke + DAST leve contra uma instância em execução.
#
# Diferente da suíte pytest, isto exercita o binário de verdade: container real,
# uvicorn real, rede real. Pega o que teste de unidade não pega — imagem sem
# arquivo, permissão errada em volume, rota que só quebra fora do TestClient.
#
# Uso:  scripts/smoke.sh [base_url]
# Ex.:  scripts/smoke.sh http://localhost:8000
#
# Roda sem STEAM_API_KEY: a conversão de SteamID não usa rede, então todas as
# asserções abaixo valem no modo offline.

set -uo pipefail

BASE="${1:-http://localhost:8000}"
SID="76561197960287930"
HASH_INEXISTENTE="0000000000000000000000000000000000000000"

falhas=0
total=0

ok()   { printf '  \033[32m✓\033[0m %s\n' "$1"; }
fail() { printf '  \033[31m✗\033[0m %s\n' "$1"; falhas=$((falhas + 1)); }

# Confere o código HTTP de uma rota contra uma lista de aceitáveis.
check_status() {
  local descricao="$1" caminho="$2" esperados="$3"
  total=$((total + 1))
  local code
  code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 --path-as-is "$BASE$caminho")
  if [[ " $esperados " == *" $code "* ]]; then
    ok "$descricao ($code)"
  else
    fail "$descricao — esperado [$esperados], recebido $code — $caminho"
  fi
}

# Confere que o corpo da resposta contém (ou não contém) um trecho.
check_body() {
  local descricao="$1" caminho="$2" modo="$3" trecho="$4"
  total=$((total + 1))
  local corpo
  corpo=$(curl -s --max-time 20 --path-as-is "$BASE$caminho")
  if [[ "$modo" == "contem" ]]; then
    if [[ "$corpo" == *"$trecho"* ]]; then ok "$descricao"; else fail "$descricao"; fi
  else
    if [[ "$corpo" != *"$trecho"* ]]; then ok "$descricao"; else fail "$descricao"; fi
  fi
}

check_header() {
  local descricao="$1" caminho="$2" cabecalho="$3" esperado="$4"
  total=$((total + 1))
  local valor
  valor=$(curl -s -o /dev/null -D - --max-time 20 "$BASE$caminho" \
          | tr -d '\r' | awk -F': ' -v h="$cabecalho" 'tolower($1)==tolower(h){print $2; exit}')
  if [[ "$valor" == *"$esperado"* ]]; then
    ok "$descricao ($cabecalho: $valor)"
  else
    fail "$descricao — $cabecalho esperado conter '$esperado', recebido '$valor'"
  fi
}

echo "── aguardando o serviço em $BASE ──"
for _ in $(seq 60); do
  curl -fsS --max-time 3 "$BASE/health" >/dev/null 2>&1 && break
  sleep 1
done
if ! curl -fsS --max-time 5 "$BASE/health" >/dev/null 2>&1; then
  echo "  serviço não respondeu a /health — abortando" >&2
  exit 1
fi

echo
echo "── disponibilidade das rotas ──"
check_status "página inicial"        "/"                                 "200"
check_status "healthcheck"           "/health"                           "200"
check_status "fragmento hello"       "/hello"                            "200"
check_status "página de salvos"      "/salvos"                           "200"
check_status "fragmento de salvos"   "/salvos/lista"                     "200"
check_status "busca htmx"            "/search?q=$SID"                    "200"
check_status "API de busca"          "/api/lookup?q=$SID"                "200"
check_status "API de salvos"         "/api/salvos"                       "200"
check_status "página de perfil"      "/perfil/$SID"                      "200"
check_status "OpenAPI"               "/openapi.json"                     "200"
check_status "CSS"                   "/static/css/style.css"             "200"
check_status "htmx vendorizado"      "/static/js/htmx.min.js"            "200"

echo
echo "── conversão de SteamID (não depende de chave) ──"
check_body "SteamID2 correto"        "/api/lookup?q=$SID" contem '"steamid2":"STEAM_1:0:11101"'
check_body "SteamID3 correto"        "/api/lookup?q=$SID" contem '"steamid3":"[U:1:22202]"'
check_body "steamid64 como string"   "/api/lookup?q=$SID" contem "\"steamid64\":\"$SID\""
check_body "account_id correto"      "/api/lookup?q=$SID" contem '"account_id":22202'
check_body "formatos convergem"      "/api/lookup?q=STEAM_0:0:11101" contem "\"steamid64\":\"$SID\""

echo
echo "── tratamento de entrada inválida ──"
check_status "lixo na API"           "/api/lookup?q=%21%21%21"           "400"
check_status "lixo no fragmento htmx" "/search?q=%21%21%21"              "200"
check_status "lixo na página perfil"  "/perfil/abacaxi%21%21%21"         "404"
check_status "offset negativo"       "/api/salvos?offset=-1"             "422"
check_status "limite fora da faixa"  "/api/salvos?limite=999"            "422"

echo
echo "── segurança: hash do avatar e path traversal ──"
check_status "hash curto"            "/avatar/aaa"                                   "400"
check_status "hash maiúsculo"        "/avatar/AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA" "400"
check_status "hash não-hex"          "/avatar/gggggggggggggggggggggggggggggggggggggggg" "400"
check_status "traversal em avatar"   "/avatar/../../etc/passwd"                      "400 404"
check_status "traversal codificado"  "/avatar/..%2F..%2Fetc%2Fpasswd"                "400 404"
check_status "traversal em estático" "/static/../config.py"                          "400 404"
check_status "traversal em estático 2" "/static/..%2F..%2Fconfig.py"                 "400 404"
check_body   "config.py inacessível" "/static/../config.py" nao_contem "STEAM_API_KEY"

echo
echo "── segurança: XSS refletido ──"
XSS='%3Cscript%3Ealert(1)%3C%2Fscript%3E'
check_body "busca escapa entrada"    "/search?q=$XSS"        nao_contem '<script>alert(1)</script>'
check_body "salvos escapa entrada"   "/salvos?q=$XSS"        nao_contem '<script>alert(1)</script>'
check_body "lista escapa entrada"    "/salvos/lista?q=$XSS"  nao_contem '<script>alert(1)</script>'

echo
echo "── segurança: injeção na sintaxe do FTS5 ──"
for termo in '%22' 'a%22%20OR%20x%20%3A%20%22b' 'NEAR(' '%2A' 'EJ%22%20OR%20%221'; do
  check_status "termo hostil: $termo" "/api/salvos?q=$termo" "200"
done

echo
echo "── segurança: a página não carrega recurso externo ──"
for rota in "/" "/salvos"; do
  total=$((total + 1))
  if curl -s --max-time 20 "$BASE$rota" | grep -qE 'src="https?://'; then
    fail "$rota carrega recurso de domínio externo"
  else
    ok "$rota sem src externo"
  fi
done

echo
echo "── cache de avatares ──"
check_header "placeholder não é cacheado" "/avatar/$HASH_INEXISTENTE" "Cache-Control" "no-store"
check_header "placeholder sinalizado"     "/avatar/$HASH_INEXISTENTE" "X-Avatar-Cache" "placeholder"

echo
echo "── superfície: métodos não suportados ──"
for rota in "/api/lookup" "/api/salvos" "/health"; do
  total=$((total + 1))
  code=$(curl -s -o /dev/null -w '%{http_code}' -X POST --max-time 20 "$BASE$rota")
  if [[ "$code" == "405" ]]; then ok "POST $rota → 405"; else fail "POST $rota → $code"; fi
done

echo
if [[ "$falhas" -eq 0 ]]; then
  printf '\033[32m%s de %s asserções passaram\033[0m\n' "$total" "$total"
else
  printf '\033[31m%s de %s asserções falharam\033[0m\n' "$falhas" "$total"
fi
exit "$falhas"
