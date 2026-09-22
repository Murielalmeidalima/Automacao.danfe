"""pacing.py — Ritmo cauteloso para não ser barrado pela cota da API.

A cota gratuita do Consulta DANFE é ~400 chaves/dia por IP. Ao estourar,
a API responde 429 com Retry-After longo (~5h de bloqueio). Para não
disparar esse bloqueio, este módulo:

  1. Guarda contadores em arquivo (ARQUIVO_CONTADOR_DIA / _HORA),
     evitando estourar o teto mesmo rodando várias vezes ao dia;
  2. O GUI consulta esses contadores para pausar ao atingir o teto da
     hora (LIMITE_CHAVES_HORA) e parar ao atingir o teto do dia
     (LIMITE_CHAVES_DIA).

O contador é aproximado: conta 1 por chave processada (a primeira
requisição da chave). Retries internos de uma mesma chave podem somar
algumas requisições extras, então o arquivo serve de freio preventivo —
o ajuste fino vem dos limites configurados.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path

import config

ARQUIVO_DIA = config.ARQUIVO_CONTADOR_DIA
ARQUIVO_HORA = config.ARQUIVO_CONTADOR_HORA


def _caminho() -> Path:
    """Devolve o caminho absoluto do arquivo de contador diário."""
    arquivo = Path(ARQUIVO_DIA)
    if not arquivo.is_absolute():
        arquivo = Path(__file__).resolve().parent / ARQUIVO_DIA
    return arquivo


def _caminho_hora() -> Path:
    """Devolve o caminho absoluto do arquivo de contador por hora."""
    arquivo = Path(ARQUIVO_HORA)
    if not arquivo.is_absolute():
        arquivo = Path(__file__).resolve().parent / ARQUIVO_HORA
    return arquivo


def _ler() -> tuple[str, int]:
    """Lê (data, consumo) do arquivo; devolve ("" , 0) se inválido/ausente."""
    caminho = _caminho()
    if not caminho.exists():
        return "", 0
    try:
        texto = caminho.read_text(encoding="utf-8").strip().split()
        return texto[0], int(texto[1])
    except (OSError, ValueError, IndexError):
        return "", 0


def _ler_hora() -> tuple[str, int]:
    """Lê (chave_da_hora, consumo) do arquivo de hora; devolve ("", 0) se vazio."""
    caminho = _caminho_hora()
    if not caminho.exists():
        return "", 0
    try:
        texto = caminho.read_text(encoding="utf-8").strip().split()
        return texto[0], int(texto[1])
    except (OSError, ValueError, IndexError):
        return "", 0


def _chave_hora(agora: datetime | None = None) -> str:
    """Identificador da janela de 60 min atual (ex.: '2026-09-22@14')."""
    agora = agora or datetime.now()
    return agora.strftime("%Y-%m-%d@%H")


def consumo_hoje() -> int:
    """Quantas chaves já foram processadas hoje por este PC/ferramenta."""
    data, consumo = _ler()
    if data != date.today().isoformat():
        return 0
    return consumo


def somar_consumo(n: int = 1) -> int:
    """Acrescenta `n` ao consumo de hoje e devolve o novo total."""
    hoje = date.today().isoformat()
    data, consumo = _ler()
    novo = (consumo if data == hoje else 0) + n
    try:
        _caminho().write_text(f"{hoje} {novo}\n", encoding="utf-8")
    except OSError:
        pass  # falha ao gravar o contador não deve derrubar o lote
    return novo


def cota_disponivel() -> int:
    """Quantas chaves ainda podem ser processadas hoje (respeitando o teto)."""
    restante = config.LIMITE_CHAVES_DIA - consumo_hoje()
    return max(0, restante)


# =============================================================================
# Controle por hora (janela de 60 min)
# =============================================================================
def consumo_hora() -> int:
    """Quantas chaves já foram processadas nesta janela de 60 min."""
    chave, consumo = _ler_hora()
    if chave != _chave_hora():
        return 0
    return consumo


def somar_consumo_hora(n: int = 1) -> int:
    """Acrescenta `n` ao consumo da hora atual e devolve o novo total."""
    chave = _chave_hora()
    _, consumo = _ler_hora()
    novo = (consumo if _ler_hora()[0] == chave else 0) + n
    try:
        _caminho_hora().write_text(f"{chave} {novo}\n", encoding="utf-8")
    except OSError:
        pass
    return novo


def cota_hora_disponivel() -> int:
    """Chaves restantes desta hora (respeitando LIMITE_CHAVES_HORA)."""
    if config.LIMITE_CHAVES_HORA <= 0:
        return 10**9
    restante = config.LIMITE_CHAVES_HORA - consumo_hora()
    return max(0, restante)


def segundos_ate_proxima_hora() -> int:
    """Segundos restantes até zerar a janela de 60 min atual."""
    agora = datetime.now()
    proxima = agora.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    return max(1, int((proxima - agora).total_seconds()))


def registrar_contador(limite: int = -1) -> str:
    """Mensagem legível com o consumo da hora e do dia (para a interface)."""
    if limite < 0:
        limite = config.LIMITE_CHAVES_DIA
    hora = config.LIMITE_CHAVES_HORA
    if hora > 0:
        base = f"Hora: {consumo_hora()}/{hora} · Dia: {consumo_hoje()}/{limite}"
    else:
        base = f"Dia: {consumo_hoje()}/{limite}"
    return f"Consumo de hoje: {base}"