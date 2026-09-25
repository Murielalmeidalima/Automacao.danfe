"""logs.py — Registro em arquivo para diagnóstico das execuções.

Escreve em `Downloads/danfe_downloader.log` todas as requisições, status
HTTP, headers de limite (Retry-After) e erros — o essencial para entender
por que a API recusou uma consulta. O arquivo gira automaticamente para
não crescer indefinidamente.

Além disso, todo erro de requisição ou erro inesperado do robô também é
gravado em `Downloads/erros_danfe.txt` (formato simples, "append", sem
girar) — é esse arquivo que deve ser enviado quando algo der errado.
"""

from __future__ import annotations

import logging
from datetime import datetime
from logging.handlers import RotatingFileHandler

import downloads

_NOME_LOGGER = "danfe"
_ARQUIVO_LOG = "danfe_downloader.log"
_ARQUIVO_ERROS = "erros_danfe.txt"
_SEPARADOR = "=" * 50
_TAMANHO_MAXIMO = 1_000_000     # ~1 MB por arquivo
_BACKUPS = 3

_configurado = False


def _configurar() -> None:
    """Anexa o handler de arquivo ao logger (executado uma única vez)."""
    global _configurado
    if _configurado:
        return

    logger = logging.getLogger(_NOME_LOGGER)
    logger.setLevel(logging.INFO)
    logger.propagate = False

    try:
        caminho = downloads.pasta_downloads() / _ARQUIVO_LOG
        handler = RotatingFileHandler(
            caminho,
            maxBytes=_TAMANHO_MAXIMO,
            backupCount=_BACKUPS,
            encoding="utf-8",
        )
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s | %(levelname)s | %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )
        logger.addHandler(handler)
    except OSError:
        # Sem permissão para gravar: a ferramenta continua funcionando,
        # apenas sem log em arquivo.
        logger.addHandler(logging.NullHandler())

    _configurado = True


def obter_logger() -> logging.Logger:
    """Devolve o logger da aplicação, já configurado para gravar em arquivo."""
    _configurar()
    return logging.getLogger(_NOME_LOGGER)


def caminho_log():
    """Retorna o caminho do arquivo de log (útil para abrir/exibir na interface)."""
    return downloads.pasta_downloads() / _ARQUIVO_LOG


def registrar_erro(titulo: str, detalhes: str = "", trace: str = "") -> None:
    """Grava um erro no arquivo `Downloads/erros_danfe.txt` (em modo append).

    Criada para diagnóstico: TODA falha de requisição à API ou erro
    inesperado do robô deve passar por aqui, gerando um bloco com carimbo
    de data/hora, título, detalhes (chave, código, mensagem, Retry-After
    etc.) e, quando houver, o traceback do Python.

    Args:
        titulo: resumo curto do que errou (ex.: "Falha na requisição à API").
        detalhes: texto multilinha explicando o erro em detalhes.
        trace: traceback (traceback.format_exc()) quando for exceção.
    """
    bloco = [f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {titulo}"]
    if detalhes:
        bloco.append(detalhes)
    if trace:
        bloco.append("Traceback:")
        bloco.append(trace)
    bloco.append(_SEPARADOR)

    try:
        caminho = downloads.pasta_downloads() / _ARQUIVO_ERROS
        with caminho.open("a", encoding="utf-8") as arquivo:
            arquivo.write("\n".join(bloco) + "\n")
    except OSError:
        pass  # sem permissão para gravar o erro: não derruba o robô


def caminho_erros():
    """Caminho do arquivo de erros (Downloads/erros_danfe.txt)."""
    return downloads.pasta_downloads() / _ARQUIVO_ERROS
