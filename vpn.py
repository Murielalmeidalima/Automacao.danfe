"""vpn.py — VPN leve integrada: descoberta, validação e rotação de IPs grátis.

A cota gratuita da API do Consulta DANFE é aplicada **por IP** (~400
chaves/dia). Para contornar o limite sem depender de um único IP, este
módulo baixa IPs grátis listados publicamente (proxies HTTP), testa quais
respondem e rotaciona o IP de saída da aplicação a cada N consultas.

O nome "VPN" é usado no sentido de *trocar o IP de saída* da aplicação —
não instala VPN de sistema. Como os IPs grátis são de datacenter, boa
parte é bloqueada pelo Cloudflare da API; quando nenhum responde, o
api_client cai automaticamente para o IP direto da máquina.

Uso na linha de comando:

    python vpn.py --testar     # valida os IPs já salvos no cache
    python vpn.py --buscar     # busca IPs grátis nas listas públicas
    python vpn.py --buscar --salvar
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import requests

import config
import logs


def normalizar(proxy: str) -> dict[str, str]:
    """Formato exigido pelo requests, aplicando o mesmo IP a http e https."""
    return {"http": proxy, "https": proxy}


def mascarar(proxy: str | None) -> str:
    """Esconde usuário:senha para uso seguro em logs."""
    if not proxy:
        return "direto"
    esquema, separador, resto = proxy.partition("://")
    if not separador:
        return proxy
    if "@" in resto:
        _, _, endereco = resto.rpartition("@")
        return f"{esquema}://***@{endereco}"
    return proxy


def _caminho_arquivo(caminho: str | None = None) -> Path:
    caminho = caminho or config.ARQUIVO_VPN
    arquivo = Path(caminho)
    if not arquivo.is_absolute():
        arquivo = Path(__file__).resolve().parent / caminho
    return arquivo


def ler_cache(caminho: str | None = None) -> list[str]:
    """Lê o cache `arq.json` de IPs vivos (mais antigo primeiro)."""
    arquivo = _caminho_arquivo(caminho)
    if not arquivo.exists():
        return []
    try:
        dados = arquivo.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    limpos = []
    for linha in dados:
        texto = linha.strip()
        if texto and not texto.startswith("#") and "://" in texto:
            limpos.append(texto)
    return limpos


def gravar_cache(lista: list[str], caminho: str | None = None) -> str:
    """Grava o cache de IPs vivos para reuso na próxima execução."""
    destino = _caminho_arquivo(caminho)
    cabecalho = (
        "# VPN grátis: IPs de saída que responderam no último teste.\n"
        "# IPs de datacenter costumam ser bloqueados pelo Cloudflare.\n"
    )
    try:
        destino.write_text(cabecalho + "\n".join(lista) + "\n", encoding="utf-8")
    except OSError:
        pass
    return str(destino)


def validar(proxy: str, timeout: float | None = None) -> tuple[bool, str]:
    """Testa o IP consultando o IP de saída (api.ipify.org).

    Returns:
        (True, "<ip-de-saida>") quando responde; (False, "<motivo>") senão.
    """
    tempo = timeout or config.TIMEOUT_TESTE_PROXY
    try:
        resp = requests.get(
            config.URL_TESTE_PROXY,
            proxies=normalizar(proxy),
            timeout=tempo,
        )
    except requests.exceptions.RequestException as exc:
        return False, type(exc).__name__
    if resp.status_code == 200:
        return True, resp.text.strip()
    return False, f"HTTP {resp.status_code}"


def _testar_paralelo(
    candidatos: list[str], timeout: float
) -> list[tuple[str, bool, str]]:
    """Executa a validação em paralelo e devolve os resultados na ordem."""
    from concurrent.futures import ThreadPoolExecutor

    if not candidatos:
        return []
    resultados: list[tuple[str, bool, str]] = []
    limites = config.MAX_TESTE_CONCORRENTE
    with ThreadPoolExecutor(max_workers=max(1, min(limites, len(candidatos)))) as pool:
        futuros = {
            pool.submit(validar, proxy, timeout): proxy for proxy in candidatos
        }
        for futuro in futuros:
            proxy = futuros[futuro]
            try:
                ok, info = futuro.result()
            except Exception as exc:  # noqa: BLE001 — teste isolado nunca derruba
                ok, info = False, type(exc).__name__
            resultados.append((proxy, ok, info))
    ordem = {proxy: i for i, proxy in enumerate(candidatos)}
    resultados.sort(key=lambda item: ordem[item[0]])
    return resultados


def _normalizar_candidato(texto: str) -> str:
    texto = texto.strip()
    if not texto or texto.startswith("#"):
        return ""
    if "://" in texto or ":" not in texto:
        return texto if "://" in texto else ""
    return "http://" + texto


def baixar_listas(fontes: list[str] | None = None) -> list[str]:
    """Baixa as listas públicas e devolve candidatos "http://ip:porta" únicos."""
    fontes = fontes or config.FONTES_VPN_GRATIS
    candidatos: list[str] = []
    vistos: set[str] = set()
    for url in fontes:
        try:
            resp = requests.get(url, timeout=15)
        except requests.exceptions.RequestException:
            continue
        if resp.status_code != 200:
            continue
        for linha in resp.text.splitlines():
            proxy = _normalizar_candidato(linha)
            if proxy and proxy not in vistos:
                vistos.add(proxy)
                candidatos.append(proxy)
    return candidatos


def buscar_ips_gratis() -> list[str]:
    """Baixa as listas grátis, testa em paralelo e devolve os que responderam."""
    logger = logs.obter_logger()
    candidatos = baixar_listas()
    if not candidatos:
        logger.warning("vpn | nenhuma lista gratuita baixada")
        return []

    limite = config.MAX_CANDIDATOS_BUSCA
    if len(candidatos) > limite:
        passo = len(candidatos) / limite
        candidatos = [candidatos[int(i * passo)] for i in range(limite)]

    logger.info(
        "vpn | baixadas %d listas | testando %d candidatos de saída",
        len(config.FONTES_VPN_GRATIS), len(candidatos),
    )
    resultados = _testar_paralelo(candidatos, config.TIMEOUT_TESTE_PROXY)
    vivos = [proxy for proxy, ok, _ in resultados if ok]
    logger.info("vpn | %d/%d IPs de saída responderam", len(vivos), len(candidatos))
    if vivos:
        gravar_cache(vivos)
    return vivos


class GerenciadorVpn:
    """Mantém o pool de IPs e o índice do IP em uso (thread-safe).

    Usado pelo api_client para escolher a saída de cada requisição e para
    trocar de IP diante de falhas (proxy morto) ou de 429 (rate limit).

    A leitura do cache é rápida e síncrona; a busca de IPs novos nas
    listas públicas roda em thread de fundo, sem bloquear as consultas —
    até os IPs chegarem, a requisição sai pelo IP direto da máquina.
    """

    def __init__(self, lista: list[str] | None = None):
        self._lock = threading.RLock()
        self._ips: list[str] = list(lista or [])
        self._indice = 0
        self._falhas: dict[str, int] = {}
        self._requisicoes = 0
        self._ultima_carga = 0.0
        self._carregado = False
        self._baixando = False

    # -- carga / recarga ---------------------------------------------------
    def _ips_excluindo_mortos(self) -> int:
        return sum(1 for ip in self._ips if self._falhas.get(ip, 0) < 2)

    def _carregar_cache(self) -> None:
        nova = ler_cache(config.ARQUIVO_VPN)
        if nova:
            self._ips = nova
            self._indice = 0

    def garantir_carregado(self) -> None:
        """Carrega o cache (rápido) e dispara busca de IPs ao fundo, se preciso."""
        logger = logs.obter_logger()
        with self._lock:
            agora = time.time()
            if not self._carregado:
                self._carregado = True
                self._ultima_carga = agora
                self._carregar_cache()
            precisa = (
                agora - self._ultima_carga > config.TEMPO_VALIDADE_VPN
                or self._ips_excluindo_mortos() < config.CACHE_VPN_MINIMO
            )
            if precisa and not self._baixando:
                self._baixando = True
                logger.info("vpn | disparando busca de IPs ao fundo")
                self._disparar_baixar()

    def _disparar_baixar(self) -> None:
        """Baixa e testa IPs em thread separada; mescla no pool quando prontos."""

        def baixar() -> None:
            logger = logs.obter_logger()
            try:
                novos = buscar_ips_gratis()
                with self._lock:
                    if novos:
                        fora = set(novos)
                        # IPs recém-testados primeiro; os antigos depois.
                        self._ips = novos + [ip for ip in self._ips if ip not in fora]
                        self._indice = 0
                logger.info("vpn | busca ao fundo concluída | novos=%d", len(novos))
            except Exception as exc:  # noqa: BLE001 — nunca derrubar o robô
                logger.warning("vpn | busca ao fundo falhou: %s", exc)
            finally:
                with self._lock:
                    self._baixando = False

        threading.Thread(target=baixar, name="vpn-baixar", daemon=True).start()

    def atualizar(self) -> tuple[int, int]:
        """Força busca síncrona (uso em CLI/thread própria) e mescla no pool.

        Returns:
            (antes, depois) — tamanho do pool antes e depois da atualização.
        """
        logger = logs.obter_logger()
        with self._lock:
            antes = len(self._ips)
        novos = buscar_ips_gratis()
        with self._lock:
            if novos:
                fora = set(novos)
                self._ips = novos + [ip for ip in self._ips if ip not in fora]
                self._indice = 0
                self._falhas = {}
                self._requisicoes = 0
            depois = len(self._ips)
            self._ultima_carga = time.time()
        logger.info("vpn | atualização manual | antes=%d | depois=%d", antes, depois)
        return antes, depois

    # -- consultas ---------------------------------------------------------
    def vazio(self) -> bool:
        with self._lock:
            return not self._ips

    def total(self) -> int:
        with self._lock:
            return len(self._ips)

    def atual(self) -> str:
        with self._lock:
            if not self._ips:
                return ""
            return self._ips[self._indice % len(self._ips)]

    def atual_normalizado(self) -> dict[str, str] | None:
        proxy = self.atual()
        return normalizar(proxy) if proxy else None

    def descricao(self) -> str:
        """Texto curto de status: 'direto' ou 'IP x/y'. Sem credenciais."""
        with self._lock:
            if not self._ips:
                return "direto"
            return f"{mascarar(self.atual())} ({self._indice % len(self._ips) + 1}/{len(self._ips)})"

    # -- rotação -----------------------------------------------------------
    def preparar_requisicao(self) -> dict[str, str] | None:
        """Retorna o proxy atual e avança para o próximo (prepara a saída
        da próxima consulta). None quando a VPN deve ser ignorada."""
        if not config.USAR_VPN or self.vazio():
            return None
        with self._lock:
            if not self._ips:
                return None
            proxy = self._ips[self._indice % len(self._ips)]
            self._requisicoes += 1
            if config.ROTACIONAR_A_CADA_N > 0 and self._requisicoes % config.ROTACIONAR_A_CADA_N == 0:
                if len(self._ips) > 1:
                    self._indice = (self._indice + 1) % len(self._ips)
            return normalizar(proxy)

    def proximo(self) -> str:
        """Avança para o próximo IP e o devolve ("" quando vazio)."""
        with self._lock:
            if not self._ips:
                return ""
            if len(self._ips) > 1:
                self._indice = (self._indice + 1) % len(self._ips)
            return self._ips[self._indice]

    def marcar_falha(self) -> None:
        """Registra falha no IP atual; após 2 falhas ele é descartado."""
        with self._lock:
            if not self._ips:
                return
            proxy = self._ips[self._indice % len(self._ips)]
            self._falhas[proxy] = self._falhas.get(proxy, 0) + 1
            if self._falhas[proxy] >= 2:
                self._ips.pop(self._indice % len(self._ips))
                if self._ips:
                    self._indice %= len(self._ips)


_gerenciador_global = GerenciadorVpn()


def obter_gerenciador() -> GerenciadorVpn:
    """Instância única compartilhada por toda a aplicação."""
    return _gerenciador_global


# =============================================================================
# Linha de comando
# =============================================================================
def _testar() -> int:
    lista = ler_cache()
    if not lista:
        print(f"Nenhum IP no cache '{config.ARQUIVO_VPN}'.")
        print("Rode: python vpn.py --buscar --salvar")
        return 1

    print(f"Testando {len(lista)} IP(s) de saída...\n")
    vivos = 0
    for proxy in lista:
        ok, info = validar(proxy)
        marca = "OK   " if ok else "FALHA"
        print(f"[{marca}] {mascarar(proxy)} -> {info}")
        vivos += int(ok)

    print(f"\n{vivos}/{len(lista)} respondendo.")
    return 0 if vivos else 1


def _buscar(salvar: bool) -> int:
    print("Buscando IPs grátis nas listas públicas...")
    aprovados = buscar_ips_gratis()
    print()
    if not aprovados:
        print("Nenhum IP grátis respondeu (esperado: datacenter bloqueado).")
        print("O app usa as consultas diretas neste caso — sem quebrar.")
        return 1

    print(f"Aprovados ({len(aprovados)}):")
    for proxy in aprovados:
        print(f"  {mascarar(proxy)}")
    if salvar:
        print(f"\nGravados em {gravar_cache(aprovados)}")
    else:
        print("\nPara gravar o cache: python vpn.py --buscar --salvar")
    return 0


if __name__ == "__main__":
    import sys

    if "--testar" in sys.argv:
        raise SystemExit(_testar())
    if "--buscar" in sys.argv:
        raise SystemExit(_buscar("--salvar" in sys.argv))
    print(__doc__)