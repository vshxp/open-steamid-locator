# Como obter e configurar a Steam Web API key

A chave é o que separa este projeto de "conversor de SteamID" para "buscador de perfis".
Sem ela a aplicação funciona — a conversão entre formatos é aritmética pura — mas nada de
nome, avatar, país, data de criação ou bans, e vanity URLs não resolvem.

- Página de registro: <https://steamcommunity.com/dev/apikey>
- Termos de uso: <https://steamcommunity.com/dev/apiterms>

## 1. Pré-requisitos

A recusa no registro quase sempre vem de um destes dois pontos:

| Requisito | Detalhe |
| --- | --- |
| Conta Steam **não limitada** | Contas limitadas não podem registrar chave. A conta deixa de ser limitada após gastar **US$ 5** (ou equivalente) na Steam — compra de jogo, adição de saldo à carteira, qualquer coisa. Presente recebido não conta; o gasto tem de ser seu. |
| **Steam Guard** ativo | A autenticação em dois fatores precisa estar habilitada na conta. |

A chave é vinculada à **conta**, não a um aplicativo, apesar de os termos falarem em
"sua Aplicação". Uma conta tem uma chave por vez.

## 2. Registrar a chave

1. Faça login na Steam e abra <https://steamcommunity.com/dev/apikey>.
2. Preencha **Domain Name**. O campo é obrigatório, mas não é verificado nem restringe de
   onde as chamadas partem — `localhost` funciona para desenvolvimento local.
   > Há relato na comunidade de que registrar um domínio real (FQDN) reduziria a incidência
   > de 429. Não é confirmado pela Valve; se você já tem um domínio, use-o — não custa nada.
3. Marque o aceite dos **Steam Web API Terms of Use**.
4. Clique em **Register**.

A chave aparece na tela: **32 caracteres hexadecimais**, algo como
`A1B2C3D4E5F60718293A4B5C6D7E8F90`. Copie na hora.

Voltando à mesma página depois, a chave é exibida novamente e há a opção de **Revoke**.
Registrar uma nova chave **substitui** a anterior — tudo que usava a antiga para de
funcionar.

## 3. Configurar no projeto

Cole a chave em `.env` na raiz do repositório:

```dotenv
STEAM_API_KEY=A1B2C3D4E5F60718293A4B5C6D7E8F90
```

Se ainda não existe `.env`, crie a partir do exemplo:

```sh
cp .env.example .env
```

Agora **recrie o container**:

```sh
docker compose up -d
```

> ⚠️ `docker compose restart` **não** serve aqui. Variáveis de ambiente são fixadas no
> momento em que o container é criado; um restart reaproveita o container antigo e a chave
> nova é ignorada. `up -d` detecta a mudança no `env_file` e recria.

O hot-reload por volume cobre apenas o código em `src/` — não o `.env`.

## 4. Verificar

### A aplicação enxerga a chave?

```sh
curl -s http://localhost:8000/health
```

```json
{"status":"ok","steam_api":true}
```

`"steam_api": false` significa que a aplicação subiu sem a chave. Reveja o passo 3 — o
suspeito mais provável é ter usado `restart` em vez de `up -d`.

A página inicial também anuncia o estado: com a chave, a dica sob o formulário passa a
dizer *"Steam Web API ativa"*.

### A chave é válida?

Busque um vanity URL — é o caminho que **só** funciona com chave:

```sh
curl -s 'http://localhost:8000/api/lookup?q=gabelogannewell' | python3 -m json.tool
```

Resolveu e trouxe o bloco `steam_api.status: "ok"` com `interpreted.persona_name`? Está
funcionando de ponta a ponta.

### Isolar chave × aplicação

Se a busca falhar, teste a chave direto contra a Steam, sem a aplicação no meio:

```sh
curl -s "https://api.steampowered.com/ISteamUser/GetPlayerSummaries/v2/?key=$STEAM_API_KEY&steamids=76561197960287930"
```

- Devolveu JSON com `players` → a chave está boa, o problema é na aplicação.
- Devolveu **403 Forbidden** → a chave está errada, revogada ou mal copiada.

## 5. Como a aplicação reage a cada falha

O campo `steam_api.status` na resposta é sempre explícito. A conversão de SteamID é
devolvida em **todos** os casos: falha da Steam nunca derruba a resposta inteira.

| `status` | Significado | O que fazer |
| --- | --- | --- |
| `skipped` | Nenhuma chave configurada | Passo 3 |
| `error` + "recusou a chave (401/403)" | Chave inválida, revogada ou com erro de digitação | Reconfira em `/dev/apikey` |
| `error` + "Rate limit (429)" | Cota ou throttle atingido | Seção 7 |
| `error` + "Timeout" / "Falha de rede" | Container sem saída para a internet, ou Steam fora | Teste `docker compose exec app curl -sI https://api.steampowered.com` |
| `not_found` | SteamID aritmeticamente válido, mas sem conta correspondente | Nada — é resposta legítima |
| `ok` | Dados obtidos | — |

## 6. Segurança

A chave age **em nome da sua conta**. Quem a tiver consome sua cota de 100.000
chamadas/dia e age sob sua responsabilidade — os termos de uso são explícitos:

> "You agree to keep your Steam Web API key confidential, and not to share it with any
> third party. […] You agree that you will be personally responsible for the use of your
> Steam Web API key."

Regras práticas:

- **`.env` nunca vai para o git.** Já está no `.gitignore` deste repositório — confirme com
  `git check-ignore -v .env` antes do primeiro commit.
- **`.env` não entra na imagem Docker.** Já está no `.dockerignore`. A chave chega ao
  container por `env_file`, em tempo de execução — não fica gravada em nenhuma camada.
- **Nunca exponha a chave ao browser.** Neste projeto todas as chamadas à Steam saem do
  servidor; o cliente só recebe HTML e JSON já processados. Mantenha assim.
- **Não coloque a chave em log, screenshot, issue ou prompt.** Se ela escapou, revogue em
  <https://steamcommunity.com/dev/apikey> e gere outra.
- A Valve pode suspender o acesso **a qualquer momento, sem aviso e sem motivo declarado** —
  é cláusula explícita dos termos. Trate a chave como recurso emprestado, não garantido.

## 7. Limites e a realidade do 429

O limite oficial nos termos de uso é **100.000 chamadas por dia, por chave**. Limites
maiores podem ser negociados com a Valve em `webapi@valvesoftware.com`.

Na prática o número oficial não é a restrição que morde. Desde março/abril de 2025 há
relatos consistentes de **429 em volumes muito abaixo da cota** — desenvolvedores
reportaram throttling fazendo uma chamada a cada 120 segundos, e até com menos de 10
chamadas por dia. Nenhuma resposta oficial da Valve. Aparecem duas teorias na comunidade,
ambas não confirmadas: throttling por reputação/IP, e "shadow ban" de padrões que parecem
scraping.

As restrições registradas na sessão de brainstorming
([`brainstorming/brainstorming-session-2026-08-11-0040.md`](brainstorming/brainstorming-session-2026-08-11-0040.md))
apontam a mesma direção, e é por isso que a arquitetura do projeto assume cache e
persistência local em vez de consultar a Steam a cada request.

Consequências de projeto que valem lembrar:

- **Assimetria de lote.** `GetPlayerSummaries` e `GetPlayerBans` aceitam **até 100 SteamIDs
  por chamada**; `GetOwnedGames` e `GetFriendList` aceitam **1**. Importar 100 perfis pode
  custar 1 request; os jogos deles custam 100.
- **Privacidade falha em silêncio.** Perfil privado devolve HTTP 200 com campos ausentes, não
  erro. A aplicação já marca isso com um `aviso` no bloco `interpreted`.
- **Sem cache, a cota é queimada em repetição.** Hoje toda busca é consulta ao vivo. A camada
  de persistência ainda não existe.

## 8. Problemas comuns

| Sintoma | Causa provável |
| --- | --- |
| A página `/dev/apikey` diz que a conta não é elegível | Conta limitada — falta gastar US$ 5 |
| A página pede para habilitar Steam Guard | 2FA desativado na conta |
| `/health` mostra `steam_api: false` com a chave no `.env` | Usou `restart` em vez de `up -d`; ou a linha tem espaços/aspas em volta do valor |
| 403 direto da Steam | Chave incorreta ou revogada — provavelmente foi substituída ao registrar outra |
| Funcionava e parou de repente | Alguém registrou nova chave na mesma conta, invalidando esta |
| 429 com pouquíssimo uso | Throttling não documentado — seção 7 |
| Timeout dentro do container | Container sem DNS/saída; teste com `curl -sI https://api.steampowered.com` de dentro dele |

## Fontes

- [Steam Web API Terms of Use](https://steamcommunity.com/dev/apiterms) — limite de 100.000
  chamadas/dia, confidencialidade da chave, direito de revogação da Valve
- [Steam Web API constantly rate-limited (Error 429)](https://steamcommunity.com/discussions/forum/1/601902348018676495)
  — relatos de 429 em volumes baixos, sem resposta oficial
- [Steam API rate limit increase](https://steamcommunity.com/discussions/forum/1/3492006259510037998/)
  — contato para limites maiores
- [The Ultimate Steam Web API Guide](https://dev.to/zuplo/the-ultimate-steam-web-api-guide-2ie8)
  — requisitos de conta e formato da chave
