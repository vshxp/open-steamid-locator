#!/usr/bin/env python3
"""Converte relatórios JSON do pip-audit em Markdown para o resumo do CI.

Vive como arquivo, e não embutido no YAML, por dois motivos: heredoc indentado
dentro de bloco YAML quebra a indentação do Python, e um script de verdade pode
ser rodado e testado localmente.

Uso:
    ci_resumo_audit.py [--apenas-total] relatorio.json [relatorio.json ...]

Com --apenas-total imprime só o número de vulnerabilidades, para alimentar um
output de step. Um relatório ausente é tratado como zero achados e sinalizado no
Markdown — o passo que gera o JSON usa continue-on-error, então a ausência
significa "o pip-audit não chegou a escrever", não "está tudo bem".
"""

from __future__ import annotations

import json
import pathlib
import sys


def carregar(caminho: pathlib.Path) -> list[dict]:
    """Devolve a lista de dependências do relatório, ou [] se ilegível."""
    try:
        dados = json.loads(caminho.read_text())
    except (OSError, ValueError):
        return []
    # O pip-audit já usou dois formatos: lista crua e objeto com "dependencies".
    if isinstance(dados, dict):
        return dados.get("dependencies", [])
    return dados if isinstance(dados, list) else []


def vulneraveis(dependencias: list[dict]) -> list[tuple[str, str, str, str]]:
    linhas = []
    for dep in dependencias:
        for v in dep.get("vulns", []) or []:
            correcoes = ", ".join(v.get("fix_versions") or []) or "sem correção publicada"
            linhas.append(
                (dep.get("name", "?"), dep.get("version", "?"), v.get("id", "?"), correcoes)
            )
    return linhas


def main(argv: list[str]) -> int:
    apenas_total = "--apenas-total" in argv
    caminhos = [pathlib.Path(a) for a in argv if not a.startswith("--")]

    achados: list[tuple[str, str, str, str]] = []
    ausentes: list[str] = []
    for caminho in caminhos:
        if not caminho.exists():
            ausentes.append(caminho.name)
            continue
        achados.extend(vulneraveis(carregar(caminho)))

    if apenas_total:
        # Falha fechada: relatório ausente significa auditoria inconclusiva, e o
        # portão do CI compara com "0". Imprimir 0 aqui transformaria um
        # pip-audit que morreu no meio em sinal verde.
        print("inconclusivo" if ausentes else len(achados))
        return 0

    print("### SCA — pip-audit")
    print()
    if ausentes:
        print(
            f"> **Auditoria inconclusiva.** Relatório ausente: `{'`, `'.join(ausentes)}` — "
            "o pip-audit não chegou a escrever o JSON. Isto **não** é sinal de que "
            "as dependências estão limpas."
        )
        print()
    if not achados:
        if not ausentes:
            print("Nenhuma vulnerabilidade conhecida nas dependências pinadas.")
        return 0

    print(f"**{len(achados)} vulnerabilidade(s).** Suba as versões abaixo.")
    print()
    print("| pacote | versão atual | aviso | corrigido em |")
    print("| --- | --- | --- | --- |")
    for nome, versao, aviso, correcoes in sorted(achados):
        print(f"| `{nome}` | `{versao}` | {aviso} | {correcoes} |")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
