"""api_client.py — Cliente da API pública do Consulta DANFE.

Concentra toda a comunicação HTTP com o endpoint /api/v1/consulta:
  1. Valida o contrato da resposta;
  2. Traduz códigos de erro (header X-Error-Code + JSON) para mensagens
     legíveis ao usuário;
3. Aplica nova tentativa com backoff exponencial para falhas transitórias
      (timeout, rede, 5xx e rate limit curto);
   4. Com a VPN leve ligada (config.USAR_VPN), rotaciona o IP de saída a
      cada consulta e troca de IP diante de 429/falhas, com fallback para
      o IP direto quando o pool de IPs grátis está vazio.
"""

from __future__ import annotations

import base64
import time
import xml.etree.ElementTree as ET

import requests

import config
import logs
import vpn


class ErroAPI(Exception):
    """Erro de domínio da API, com mensagem pronta para exibição."""

    def __init__(self, mensagem: str, codigo: str = ""):
        super().__init__(mensagem)
        self.mensagem = mensagem
        self.codigo = codigo


# =============================================================================
# Mensagens padronizadas por código de erro da API
# =============================================================================
_MENSAGENS_ERRO = {
    "chave_obrigatoria": "Chave de acesso é obrigatória.",
    "chave_invalida": "Chave inválida: use 44 caracteres entre A-Z e 0-9.",
    "dv_invalido": "Chave inválida: dígito verificador incorreto.",
    "tipo_nao_suportado": "Tipo de documento não suportado.",
    "nao_encontrada": "DANFE não encontrada na SEFAZ.",
    "pendente": "NF-e pendente ou em contingência; tente novamente depois.",
    "servico_indisponivel": "Serviço temporariamente indisponível.",
    "erro_interno": "Erro interno na API.",
}


def _mensagem_por_codigo(codigo: str, fallback: str) -> str:
    return _MENSAGENS_ERRO.get(codigo, fallback)


def _formatar_espera(segundos: float) -> str:
    """Converte segundos em um texto curto e legível (ex.: '2h 30min')."""
    total = max(0, int(round(segundos)))
    if total < 60:
        return f"{total}s"
    minutos, resto = divmod(total, 60)
    horas, minutos = divmod(minutos, 60)
    if horas:
        return f"{horas}h {minutos}min"
    return f"{minutos}min {resto}s"


def _retry_after_segundos(resp: requests.Response) -> float | None:
    """Lê o header Retry-After em segundos; None quando ausente/inválido."""
    valor = resp.headers.get("Retry-After")
    if not valor:
        return None
    try:
        return float(valor)
    except (TypeError, ValueError):
        return None


def _pode_trocar_ip(
    gerenciador: vpn.GerenciadorVpn,
    trocas: int,
    por_rate_limit: bool = False,
) -> bool:
    """Indica se ainda vale trocar o IP de saída (há outra saída e orçamento).

    `por_rate_limit=True` só permite a troca quando ROTACIONAR_EM_429 está
    ligado; erros de conexão do próprio IP sempre podem rotacionar.
    """
    if not config.USAR_VPN:
        return False
    if por_rate_limit and not config.ROTACIONAR_EM_429:
        return False
    if trocas >= config.MAX_TROCA_VPN_POR_CHAVE:
        return False
    return gerenciador.total() > 1


def _extrair_nnf(xml_base64: str) -> str:
    """Tenta ler o número da nota (<nNF>) a partir do XML retornado.

    Retorna "" quando o XML não está presente ou não pôde ser lido.
    """
    if not xml_base64:
        return ""
    try:
        raiz = ET.fromstring(base64.b64decode(xml_base64))
    except (ET.ParseError, ValueError, base64.binascii.Error):
        return ""
    for elemento in raiz.iter():
        if elemento.tag.rsplit("}", 1)[-1] == "nNF" and elemento.text:
            texto = elemento.text.strip()
            if texto.isdigit():
                return str(int(texto))
    return ""


def consultar_danfe(chave: str) -> dict:
    """Consulta o DANFE de uma chave na API, com retries por falha transitória.

    Toda falha (rate limit, timeout, rede, resposta inválida, PDF
    corrompido, fim das tentativas) também é gravada em
    `Downloads/erros_danfe.txt` para diagnóstico.

    Args:
        chave: chave de acesso de 44 caracteres (já validada pelo chamador).

    Returns:
        dict com:
          {ok: True,  chave, numero, pdf_bytes, tipo}                em sucesso;
          {ok: False, chave, numero ("" ou valor), mensagem, codigo} em falha.
    """
    resposta = _consultar_danfe_impl(chave)
    if not resposta["ok"]:
        logs.registrar_erro(
            "Falha na requisição a API",
            _detalhes_erro(chave, resposta),
        )
    return resposta


def _detalhes_erro(chave: str, resposta: dict) -> str:
    """Monta o texto multilinha gravado no arquivo de erros."""
    linhas = [
        f"Chave: {chave}",
        f"Código: {resposta.get('codigo') or '(sem código)'}",
        f"Mensagem: {resposta.get('mensagem') or ''}",
    ]
    retry_after = resposta.get("retry_after")
    if retry_after is not None:
        linhas.append(
            f"Retry-After: {retry_after:.0f}s "
            f"({_formatar_espera(retry_after)}) "
            f"[429 = limite de requisições da API]"
        )
    linhas.append(f"HTTP status: {resposta.get('http_status', '')}")
    linhas.append(f"X-Error-Code: {resposta.get('x_error_code', '')}")
    return "\n".join(linhas)


def _consultar_danfe_impl(chave: str) -> dict:
    logger = logs.obter_logger()
    gerenciador = vpn.obter_gerenciador()
    trocas_ip = 0
    ultimo_erro: ErroAPI | None = None
    tentativa = 0

    if config.USAR_VPN:
        gerenciador.garantir_carregado()

    while tentativa < config.MAX_TENTATIVAS:
        tentativa += 1

        # Saída: VPN leve (IP rotativo) ou direto quando o pool está vazio.
        opcoes: dict = {
            "json": {"chave": chave, "format": "json"},
            "headers": config.HEADERS,
            "timeout": (config.TIMEOUT_CONEXAO_SEGUNDOS, config.TIMEOUT_SEGUNDOS),
        }
        proxy_atual = ""
        if config.USAR_VPN:
            proxies_dict = gerenciador.preparar_requisicao()
            if proxies_dict:
                opcoes["proxies"] = proxies_dict
                proxy_atual = list(proxies_dict.values())[0]

        try:
            resp = requests.post(config.ENDPOINT_CONSULTA, **opcoes)
        except requests.exceptions.ProxyError as exc:
            logger.warning(
                "falha de IP de saída | chave=%s | ip=%s | %s",
                chave, vpn.mascarar(proxy_atual), exc,
            )
            gerenciador.marcar_falha()
            if _pode_trocar_ip(gerenciador, trocas_ip):
                trocas_ip += 1
                gerenciador.proximo()
                tentativa -= 1  # trocar de IP não consome tentativa normal
                continue
            ultimo_erro = ErroAPI(
                "Falha de comunicação com a API (IP de saída).",
                "falha_comunicacao",
            )
            retry = _dependente_de_retry_falha(ultimo_erro.codigo, tentativa)
            if retry:
                time.sleep(retry)
            continue
        except requests.exceptions.Timeout:
            ultimo_erro = ErroAPI(
                "Tempo de resposta excedido (timeout).", "timeout"
            )
            logger.warning("timeout | chave=%s | tentativa=%d", chave, tentativa)
            retry = _dependente_de_retry_falha(ultimo_erro.codigo, tentativa)
            if retry:
                time.sleep(retry)
            continue
        except requests.exceptions.RequestException as exc:
            ultimo_erro = ErroAPI(
                "Falha de comunicação com a API.", "falha_comunicacao"
            )
            logger.warning(
                "falha de rede | chave=%s | tentativa=%d | %s",
                chave, tentativa, exc,
            )
            retry = _dependente_de_retry_falha(ultimo_erro.codigo, tentativa)
            if retry:
                time.sleep(retry)
            continue
        except Exception as exc:  # noqa: BLE001 — fronteira de rede: nunca deixar vazar
            return {
                "ok": False,
                "chave": chave,
                "numero": "",
                "mensagem": f"Falha de comunicação com a API ({type(exc).__name__}).",
                "codigo": "falha_comunicacao",
            }

        # ---- resposta HTTP recebida --------------------------------------
        if resp.status_code == 200:
            try:
                dados = resp.json()
            except ValueError as exc:
                return {
                    "ok": False,
                    "chave": chave,
                    "numero": "",
                    "mensagem": "Resposta inválida (formato inesperado).",
                    "codigo": "resposta_invalida",
                    "http_status": resp.status_code,
                    "x_error_code": "",
                }

            if dados.get("status") == "ok" and dados.get("pdf_base64"):
                try:
                    pdf_bytes = base64.b64decode(dados["pdf_base64"], validate=True)
                except (ValueError, base64.binascii.Error) as exc:
                    return {
                        "ok": False,
                        "chave": chave,
                        "numero": "",
                        "mensagem": "Falha no download: PDF retornado em formato incorreto.",
                        "codigo": "pdf_base64_invalido",
                        "http_status": resp.status_code,
                        "x_error_code": "",
                    }
                if not pdf_bytes.startswith(config.MAGIC_PDF):
                    return {
                        "ok": False,
                        "chave": chave,
                        "numero": "",
                        "mensagem": "Falha no download: PDF retornado vazio ou corrompido.",
                        "codigo": "pdf_corrompido",
                        "http_status": resp.status_code,
                        "x_error_code": "",
                    }
                numero = _extrair_nnf(dados.get("xml_base64") or "") or chave[25:34]
                logger.info("200 OK | chave=%s | tentativa=%d", chave, tentativa)
                return {
                    "ok": True,
                    "chave": chave,
                    "numero": str(int(numero)) if str(numero).isdigit() else numero,
                    "pdf_bytes": pdf_bytes,
                    "tipo": dados.get("tipo", ""),
                }

            erro = _extrair_mensagem_erro(dados, resp)
            return {
                "ok": False,
                "chave": chave,
                "numero": "",
                "mensagem": erro.mensagem,
                "codigo": erro.codigo,
                "http_status": resp.status_code,
                "x_error_code": resp.headers.get("X-Error-Code", ""),
            }

        # ---- erro HTTP (4xx/5xx) -----------------------------------------
        erro = _erro_por_status(resp)
        if resp.status_code == 429:
            espera = _retry_after_segundos(resp)
            logger.warning(
                "429 rate limit | chave=%s | tentativa=%d | Retry-After=%s | ip=%s",
                chave, tentativa,
                espera if espera is not None else "ausente",
                vpn.mascarar(proxy_atual),
            )
            # O limite é por IP: com VPN ligada, trocar a saída costuma
            # resolver tanto o 429 curto quanto o "duro" (cota do dia).
            if _pode_trocar_ip(gerenciador, trocas_ip, por_rate_limit=True):
                trocas_ip += 1
                gerenciador.marcar_falha()
                novo = gerenciador.proximo()
                logger.info(
                    "trocando IP por rate limit | chave=%s | novo=%s",
                    chave, vpn.mascarar(novo),
                )
                tentativa -= 1  # trocar de IP não consome tentativa normal
                continue
            # Sem VPN / pool vazio: 429 com espera longa = cota do dia/IP
            # bloqueado. Esperar não resolve — devolve de imediato para o
            # lote ser interrompido sem queimar mais requisições.
            if espera is not None and espera > config.RETRY_429_MAX_ESPERA:
                return {
                    "ok": False,
                    "chave": chave,
                    "numero": "",
                    "mensagem": (
                        "Limite de requisições atingido. A API pede para "
                        f"aguardar {_formatar_espera(espera)} antes de tentar "
                        "novamente."
                    ),
                    "codigo": "rate_limit_longo",
                    "retry_after": espera,
                    "http_status": resp.status_code,
                    "x_error_code": resp.headers.get("X-Error-Code", ""),
                }
            # 429 curto (janela por minuto): aguarda e tenta de novo.
            if tentativa < config.MAX_TENTATIVAS:
                espera_retry = espera if espera else min(
                    config.BACKOFF_MAX,
                    config.BACKOFF_BASE ** (tentativa - 1),
                )
                time.sleep(min(espera_retry, config.RETRY_429_CAP))
                continue
        elif resp.status_code >= 500:
            # Falhas de servidor: nova tentativa com backoff exponencial.
            if tentativa < config.MAX_TENTATIVAS:
                time.sleep(min(
                    config.BACKOFF_MAX,
                    config.BACKOFF_BASE ** (tentativa - 1),
                ))
                continue
        else:
            # Erros 4xx determinísticos (400/413/422/404/etc.): sem retry.
            pass

        ultimo_erro = erro

    # Esgotou as tentativas.
    return {
        "ok": False,
        "chave": chave,
        "numero": "",
        "mensagem": ultimo_erro.mensagem,
        "codigo": ultimo_erro.codigo,
    }


def _extrair_mensagem_erro(dados: dict, resp: requests.Response) -> ErroAPI:
    """Monta a mensagem de erro a partir do JSON `{error, message}` e headers."""
    codigo = dados.get("error") or resp.headers.get("X-Error-Code", "")
    mensagem = dados.get("message") or _mensagem_por_codigo(
        codigo, f"Erro ao consultar a chave (HTTP {resp.status_code})."
    )
    return ErroAPI(mensagem, codigo)


def _erro_por_status(resp: requests.Response) -> ErroAPI:
    """Converte o status HTTP em um ErroAPI com mensagem legível."""
    if resp.status_code == 404:
        return ErroAPI("DANFE não encontrada.", "nao_encontrada")
    if resp.status_code == 429:
        return ErroAPI("Limite de requisições atingido.", "rate_limit_exceeded")
    if resp.status_code == 503:
        return ErroAPI("Serviço temporariamente indisponível.", "servico_indisponivel")
    if resp.status_code == 202:
        return ErroAPI("NF-e pendente; tente novamente.", "pendente")
    codigo = resp.headers.get("X-Error-Code", "")
    mensagem = _mensagem_por_codigo(
        codigo, f"Erro na API (HTTP {resp.status_code})."
    )
    return ErroAPI(mensagem, codigo)


def _dependente_de_retry_falha(codigo: str, tentativa: int) -> float:
    """Backoff exponencial para timeout/falha de rede nas tentativas seguintes."""
    if tentativa >= config.MAX_TENTATIVAS:
        return 0.0
    return min(config.BACKOFF_MAX, config.BACKOFF_BASE ** (tentativa - 1))