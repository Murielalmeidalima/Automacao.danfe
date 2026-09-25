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

## VPN grátis (rotação de IP de saída)

Como o limite da API é por IP, a ferramenta traz uma **VPN leve integrada**
(`vpn.py`): ela baixa IPs grátis de listas públicas, testa quais respondem e
**rotaciona o IP de saída** a cada consulta — espalhando as ~400 chaves/dia
entre vários IPs. Quando a API responde `429`, ela troca o IP na hora em vez de
encerrar o lote.

- **Tudo automático** (sem controles na tela): ligue/desligue só pelo
  `USAR_VPN` no `config.py` (padrão ligado). A busca de IPs roda sozinha,
  em segundo plano, e a rotação acontece a cada consulta sem intervenção.
- IPs de datacenter são, em boa parte, **bloqueados pelo Cloudflare** da API —
  quando nenhum responde, as consultas **caem automaticamente para o IP
  direto** da máquina (a ferramenta não quebra).
- Os IPs encontrados são salvos em `vpn_cache.txt` (não versionado) para reuso.
- Linha de comando (opcional, para diagnóstico): `python vpn.py --buscar` e
  `python vpn.py --testar`.
- **Atenção:** os dados das notas trafegam por servidores de terceiros ao usar
  a VPN. Use apenas se aceitar isso — para dados sensíveis, prefira consultas
  diretas respeitando a cota.

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
2. Na aba **Actions** do repositório, abra **"Atualizado Baixador de NFS"** e
   clique em **"Run workflow"**.
3. Aguarde o build (~2–4 min), abra o job e baixe o artefato
   **BaixadorDANFE** — dentro dele está o `BaixadorDANFE.exe`.

### Como rodar o .exe

1. Salve `BaixadorDANFE.exe` em qualquer pasta do PC.
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
