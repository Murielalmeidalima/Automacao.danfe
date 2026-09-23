"""config.py — Constantes e configurações centrais da aplicação.

Centraliza URLs da API, timeouts, delays e políticas de nova tentativa para
que os demais módulos não repitam valores soltos.
"""

# =============================================================================
# API pública do Consulta DANFE (sem chave de API, sem cadastro)
# =============================================================================
API_BASE_URL = "https://consultadanfe.com"
ENDPOINT_CONSULTA = f"{API_BASE_URL}/api/v1/consulta"
HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json",
    "User-Agent": "BaixadorDANFE/1.0",
}

# =============================================================================
# Requisições HTTP
# =============================================================================
TIMEOUT_SEGUNDOS = 30          # tempo máximo para cada chamada à API
TIMEOUT_CONEXAO_SEGUNDOS = 10  # tempo de conexão; cai rápido se a rede falhar
DELAY_ENTRE_CHAVES = 1.2       # intervalo entre chaves (respeita 60 req/min)

# =============================================================================
# Nova tentativa (retry)
# =============================================================================
MAX_TENTATIVAS = 4             # total de tentativas por chave (1 primeira + retries)
BACKOFF_BASE = 2               # multiplicador exponencial: 2s, 4s, 8s...
BACKOFF_MAX = 8                # teto do backoff em segundos

# Rate limit (HTTP 429):
#   A API devolve o header Retry-After. Quando ele é curto (janela por minuto),
#   vale a pena aguardar e tentar de novo. Quando é longo (cota do dia/IP
#   bloqueado), esperar não resolve — o lote deve ser interrompido.
RETRY_429_MAX_ESPERA = 120     # acima disso o 429 é tratado como limite "duro"
RETRY_429_CAP = 120            # teto (em s) de espera em um 429 retentável

# =============================================================================
# Ritmo cauteloso (exibição + teto diário)
# =============================================================================
#   A cota gratuita é ~400 chaves/dia por IP e, ao estourar o dia, a API
#   bloqueia com 429 longo (~5h). O único teto rígido a NÃO ultrapassar é o
#   por minuto (~60/min) — garantido pelo DELAY_ENTRE_CHAVES. O limite por
#   hora é apenas INFORMATIVO na tela: passar um pouco dele não bloqueia,
#   desde que não se exceda o teto de 60 req/min. O teto diário para o lote.
RITMO_CAUTELOSO = True         # mostra cota na tela e força o teto diário
LIMITE_CHAVES_HORA = 50        # exibição (informa o ritmo de 400 em 8h)
LIMITE_CHAVES_DIA = 400        # teto diário: para o lote
ARQUIVO_CONTADOR_DIA = "contador_diario.txt"   # registra o consumo do dia
ARQUIVO_CONTADOR_HORA = "contador_hora.txt"    # registra o consumo da hora

# =============================================================================
# Arquivos / pastas
# =============================================================================
PREFIXO_PDF = "NF_"
PREFIXO_RELATORIO = "relatorio_danfes"
MAGIC_PDF = b"%PDF-"           # assinatura usada para validar o arquivo baixado

# Status possíveis para o relatório
STATUS_SUCESSO = "sucesso"
STATUS_FALHA = "falha"