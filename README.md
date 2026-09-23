# Baixador de DANFE

Aplicação desktop (Python + CustomTkinter) que baixa DANFEs de várias notas
fiscais de uma vez usando a **API pública gratuita** do
[Consulta DANFE](https://consultadanfe.com/api), a partir apenas da
**chave de acesso (44 dígitos)** de cada NF-e — sem XML.

Os PDFs são salvos na pasta **Downloads** do Windows com o nome
`NF_<numero_da_nota>.pdf` (o número da nota é lido do próprio retorno da API).
Caso o arquivo já exista, é criado `NF_<numero> (1).pdf`, `NF_<numero> (2).pdf`,
etc. — sem sobrescrever. Ao final, um relatório `relatorio_danfes_<data>.xlsx`
com o resultado de cada chave também é salvo na Downloads.

## Fluxo

1. Cole várias chaves de acesso (uma por linha).
2. Clique em **Processar DANFEs**.
3. Cada chave é validada (44 dígitos + dígito verificador) e consultada na API.
4. O PDF é baixado e renomeado com o número da nota.
5. Barra de progresso + contadores em tempo real (processadas / sucesso / falhas).
6. Painel de **cota gratuita** em tempo real (usadas hoje / disponíveis hoje / hora / próxima janela).
7. Resumo final na própria tela + relatório `relatorio_danfes_<data>.xlsx`.

## Recursos da interface

- **Processar DANFEs** — executa a consulta das chaves.
- **Painel de cota** — mostra o consumo do dia (usadas/disponíveis), o consumo da hora atual e o tempo até a próxima janela de 60 min.
- **Copiar Chaves com Erro** — copia apenas as chaves que falharam (para nova tentativa).
- **Abrir Downloads** — abre a pasta de Downloads.
- **Nova Consulta** — limpa a tela para uma nova execução.
- **Fechar** — encerra o programa.

## Limites da API e ritmo cauteloso

O plano gratuito da API tem **~60 requisições/minuto** e **~400 chaves/dia por IP**.
Para não ser barrado, a ferramenta usa um **ritmo cauteloso** (ajustável em
`config.py`):

- Delay de **1,2s entre chaves** — nunca passa de 60 req/min (o único teto que
  não pode ser ultrapassado).
- **Teto diário** (`LIMITE_CHAVES_DIA`, padrão 400): ao atingir, o lote para e
  avisa — evitando o bloqueio de ~5h que a API aplica quando a cota do dia é
  estourada.
- **Teto por hora** (`LIMITE_CHAVES_HORA`, padrão 50) é **apenas informativo**
  na tela: passar um pouco da hora é aceitável, desde que o por minuto não seja
  excedido.
- O consumo é **persistido** em `contador_diario.txt`/`contador_hora.txt` — o
  teto do dia vale mesmo fechando e abrindo o programa várias vezes.

Com **proxies** a cota se multiplica (um teto por IP) — veja *Rotação de IP*.

> A rotação de proxies roda **de forma anônima/automática** em segundo plano:
> não há botões nem indicador na tela. Para repor a lista manualmente, use
> `python proxies.py --buscar --salvar` (ver seção *Rotação de IP*).

## Rotação de IP (proxies)

**Opcional.** O ritmo cauteloso já basta para respeitar os limites de **um** IP
(sua rede). Como a cota gratuita é aplicada **por IP**, os proxies servem para
**multiplicar** a capacidade: com N proxies você roda até ~400 × N chaves/dia.
O recurso vem **desligado** por padrão.

### Como ativar

1. Crie o arquivo **`proxies.txt`** na pasta do projeto (copie de
   `proxies.exemplo.txt`), com **um proxy por linha**:

   ```
   http://usuario:senha@host:porta
   http://usuario:senha@host2:porta
   host3:porta
   ```

   > `proxies.txt` contém credenciais e **não deve ser versionado** (já está no
   > `.gitignore`).

2. Ajuste em `config.py`:

   ```python
   USAR_PROXIES = True            # liga a rotação
   ROTACIONAR_EM_429 = True       # troca de proxy ao bater rate limit
   ROTACIONAR_A_CADA_N = 0        # 0 = só troca em erro; N>0 = a cada N requests
   ```

3. (Opcional) teste os proxies antes de rodar:

   ```bash
   python proxies.py --testar
   ```

### Como funciona

- Ao receber **429 (rate limit)** ou erro de proxy, a ferramenta **troca para o
  próximo proxy** e tenta a mesma chave de novo — até
  `MAX_TROCA_PROXY_POR_CHAVE` trocas.
- Esgotadas as trocas sem sucesso, o comportamento é o mesmo de quando não há
  proxies: o lote é interrompido e informa o tempo de espera da API.
- O rótulo **Proxy** na tela mostra o proxy em uso; **Recarregar proxies** lê o
  arquivo novamente.
- Credenciais de proxy são **mascaradas** nos logs (`http://***@host:porta`).

### Proxies grátis (automático)

Como o projeto prioriza custo zero, há uma busca automática de proxies grátis:
ela baixa listas públicas, testa cada candidato em dois estágios
(conectividade + acesso ao alvo/Cloudflare) e grava **apenas os que funcionam**.

- Na interface: botão **Buscar proxies grátis** (roda em segundo plano e
  atualiza o `proxies.txt` sozinho).
- Pela linha de comando:

  ```bash
  python proxies.py --buscar            # mostra os aprovados
  python proxies.py --buscar --salvar   # grava os aprovados em proxies.txt
  ```

> **Aviso:** proxies grátis são **instáveis** (duram de minutos a horas) e, em
> boa parte, são bloqueados pelo Cloudflare. A busca contorna isso testando
> contra o alvo, mas espere precisar **rodar a busca de novo com frequência**.
> Para uso sério/400 consultas por dia, proxies **residenciais pagos** (a partir
> de ~US$1/GB) são bem mais estáveis.

#### Renovação automática durante o lote

Para não parar no limite diário por IP, quando **todos** os proxies atuais
atingem o limite, a ferramenta **busca novos proxies grátis sozinha** e
continua a mesma chave (`AUTO_RENOVAR_PROXIES = True`), até
`MAX_RENOVACOES_POR_CHAVE` vezes por chave. Só interrompe o lote se, mesmo
renovando, o limite persistir (ou não houver nenhum proxy funcional).

> **Escolha dos proxies:** a API está atrás de Cloudflare, então proxies de
> **datacenter e listas grátis costumam ser bloqueados**. Use proxies
> **residenciais/móveis** para ter IPs aceitos.
>
> Atenção: contornar o limite por IP com rotação contraria os Termos de Uso do
> serviço — o risco (bloqueio/banimento) é de quem usa.

## Instalação (para desenvolvedor)

Requer Python 3.10+ com `venv`.

```bash
python -m venv .venv
# Windows:
.venv\Scripts\activate
# Linux/macOS:
source .venv/bin/activate

pip install -r requirements.txt
python main.py
```

> Dica: no Windows basta dar duplo clique em `abrir_danfe.bat` — ele cria o
> ambiente, instala as dependências e abre o programa sozinho.

## Gerar o executável único (.exe) — para enviar via WhatsApp

Existem **duas formas** de gerar o `BaixadorDANFE.exe`. O resultado é o
mesmo: um único arquivo que roda em qualquer Windows **sem Python instalado**.

### Opção 1 — `gerar_exe.bat` (1 duplo clique, com ou sem Python)

No PC **Windows** (pode ser qualquer um), dê duplo clique em **`gerar_exe.bat`**:

- Se houver Python → usa o existente.
- Se **não houver Python** → o próprio script **instala automaticamente**
  (via `winget`, ou baixando o instalador oficial do python.org em modo
  silencioso, sem precisar de administrador) e depois compila.

Em ~2–5 minutos é gerado:

```
dist\BaixadorDANFE.exe
```

### Opção 2 — GitHub Actions (build na nuvem, NENHUM Python necessário)

Ideal quando não há nenhum Windows com Python disponível. O build roda na
nuvem do GitHub (que já tem Python):

1. Crie/use um repositório no GitHub e suba a pasta `danfe_downloader`
   (o arquivo de pipeline já está em `.github/workflows/gerar-exe.yml`).
2. Na aba **Actions** do repositório, abra **"Gerar BaixadorDANFE.exe"** e
   clique em **"Run workflow"**.
3. Aguarde o build (~2–4 min), abra o job e baixe o artefato
   **BaixadorDANFE** — dentro dele está o `BaixadorDANFE.exe`.

### Como rodar o .exe

1. Salve `BaixadorDANFE.exe` em qualquer pasta do PC da empresa.
2. Duplo clique para abrir.
3. Se o Windows SmartScreen avisar "Protegido/Desconhecido" (arquivo sem
   assinatura digital), clique em **Mais informações → Executar mesmo assim**.

## API utilizada

| Item | Valor |
|---|---|
| Endpoint | `POST https://consultadanfe.com/api/v1/consulta` |
| Corpo | `{"chave": "<44 digitos>"}` |
| Resposta | `{status, chave, tipo, pdf_base64, xml_base64}` |
| Limite | ~60 req/min · ~400 chaves/dia por IP (a ferramenta usa ritmo cauteloso e mostra a cota na tela) |
| Erros | `{error, message}` + header `X-Error-Code` / `Retry-After` |

> **Atenção ao plano gratuito:** o limite é aplicado **por IP** e é
> **compartilhado** com qualquer outra pessoa/aba que use o consultadanfe.com
> na mesma rede (site e extensão contam junto). Ao exceder, a API responde
> `429` com o header `Retry-After` — que pode indicar **horas** de espera
> (cota diária/IP bloqueado). Nesse caso a ferramenta **interrompe o lote**
> imediatamente em vez de ficar tentando, e informa o tempo de espera.

## Tratamento de erros

- Chave com quantidade incorreta de dígitos ou inválida (dígito verificador);
- Falha de comunicação com a API e timeout (nova tentativa com backoff);
- Rate limit (429) **curto** (janela por minuto) — aguarda o `Retry-After` e tenta novamente;
- Rate limit (429) **longo** (cota diária / IP bloqueado) — **interrompe o lote**
  e informa quanto tempo a API pede para aguardar;
- DANFE não encontrada (404);
- PDF retornado vazio/corrompido (falha no download);
- Erro de gravação do arquivo (permissão, disco cheio) — registrado no relatório.

## Observações

- A consulta atende apenas NF-e (modelo 55) pela chave; o número da nota é
  extraído do `xml_base64` retornado pela API (fallback: posição 26–34 da chave).
- Se já existir um `NF_<numero>.pdf` na pasta, a ferramenta cria
  `NF_<numero> (1).pdf`, `NF_<numero> (2).pdf` etc., sem sobrescrever.
- O IP da rede onde a ferramenta roda é compartilhado entre os usuários:
  o limite é por IP, então evite rodar simultaneamente com outras ferramentas
  no mesmo IP.
- **Log de diagnóstico:** toda execução grava em
  `Downloads/danfe_downloader.log` o status de cada requisição, o header
  `Retry-After` e as mensagens de erro — use esse arquivo para entender
  recusas da API.