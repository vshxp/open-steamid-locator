#!/usr/bin/env python3
"""Resume um relatório JSON do trivy em Markdown para o resumo do CI.

Separa o que é **acionável** (tem versão corrigida publicada) do que não é. Essa
distinção é o ponto: CVE de pacote do sistema operacional na imagem base
frequentemente não tem correção disponível, e falhar o pipeline nela o deixa
permanentemente vermelho — o que treina todo mundo a ignorar o pipeline. O portão
gasta seu crédito no acionável; o resto fica visível no relatório.

Uso:
    ci_resumo_trivy.py relatorio.json [--titulo "..."] [--apenas-total-acionavel]
"""

from __future__ import annotations

import json
import pathlib
import sys

ORDEM = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3, "UNKNOWN": 4}


def coletar(caminho: pathlib.Path) -> tuple[list[dict], list[dict]]:
    """Devolve (acionáveis, sem_correcao)."""
    try:
        dados = json.loads(caminho.read_text())
    except (OSError, ValueError):
        return [], []

    acionaveis: list[dict] = []
    sem_correcao: list[dict] = []
    for resultado in dados.get("Results") or []:
        for v in resultado.get("Vulnerabilities") or []:
            (acionaveis if v.get("FixedVersion") else sem_correcao).append(v)
    return acionaveis, sem_correcao


def tabela(vulns: list[dict], com_correcao: bool) -> None:
    cabecalho = "| severidade | CVE | pacote | instalada |"
    separador = "| --- | --- | --- | --- |"
    if com_correcao:
        cabecalho += " corrigida em |"
        separador += " --- |"
    print(cabecalho)
    print(separador)
    for v in sorted(
        vulns,
        key=lambda x: (ORDEM.get(x.get("Severity", "UNKNOWN"), 9), x.get("VulnerabilityID", "")),
    ):
        linha = (
            f"| {v.get('Severity')} | {v.get('VulnerabilityID')} "
            f"| `{v.get('PkgName')}` | `{v.get('InstalledVersion')}` |"
        )
        if com_correcao:
            linha = linha[:-1] + f" `{v.get('FixedVersion')}` |"
        print(linha)


def main(argv: list[str]) -> int:
    apenas_total = "--apenas-total-acionavel" in argv
    titulo = "Imagem — trivy"
    if "--titulo" in argv:
        titulo = argv[argv.index("--titulo") + 1]
    posicionais = [a for a in argv if not a.startswith("--") and a != titulo]
    if not posicionais:
        print("uso: ci_resumo_trivy.py relatorio.json", file=sys.stderr)
        return 2

    caminho = pathlib.Path(posicionais[0])
    if not caminho.exists():
        # Fail-closed, igual ao resumo do pip-audit: relatório ausente é
        # varredura inconclusiva, não ausência de problema.
        print(
            "inconclusivo"
            if apenas_total
            else f"### {titulo}\n\n> **Varredura inconclusiva**: relatório ausente."
        )
        return 0

    acionaveis, sem_correcao = coletar(caminho)

    if apenas_total:
        print(len(acionaveis))
        return 0

    print(f"### {titulo}")
    print()
    if not acionaveis and not sem_correcao:
        print("Nenhuma vulnerabilidade na severidade avaliada.")
        return 0

    if acionaveis:
        print(f"**{len(acionaveis)} acionável(is)** — há versão corrigida publicada.")
        print()
        tabela(acionaveis, com_correcao=True)
        print()
    else:
        print(
            "Nenhuma vulnerabilidade **acionável**: não há correção publicada para as encontradas."
        )
        print()

    if sem_correcao:
        rotulo = f"{len(sem_correcao)} sem correção disponível (não bloqueiam)"
        print(f"<details><summary>{rotulo}</summary>")
        print()
        tabela(sem_correcao, com_correcao=False)
        print()
        print("</details>")
        print()
        print(
            "Estas vêm de pacotes do sistema operacional da imagem base. Resolvem-se "
            "quando o upstream publica correção e a imagem base é reconstruída — não "
            "por mudança neste repositório."
        )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
