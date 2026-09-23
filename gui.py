"""gui.py — Interface gráfica (CustomTkinter) do Baixador de DANFE.

A interface roda no thread principal; o processamento das chaves roda em
um thread em segundo plano que publica eventos em uma fila (queue.Queue).
A interface consome essa fila periodicamente via after(), mantendo a
janela responsiva durante toda a execução.
"""

from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import customtkinter as ctk

import api_client
import chave
import config
import downloads
import pacing
import report

ctk.set_appearance_mode("system")
ctk.set_default_color_theme("blue")


@dataclass
class Contadores:
    """Contadores de progresso compartilhados entre o worker e a UI."""

    total: int = 0
    processadas: int = 0
    sucesso: int = 0
    falhas: int = 0
    resultado: list[dict] = field(default_factory=list)
    falhas_chaves: list[str] = field(default_factory=list)
    baixadas: list[str] = field(default_factory=list)


class Aplicacao(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()

        self.title("Baixador de DANFE — Consulta DANFE Online")
        self.geometry("780x760")
        self.minsize(720, 680)

        self._fila = queue.Queue()          # fila de eventos do worker
        self._contadores = Contadores()     # estado compartilhado
        self._processando = False

        self._construir_widgets()
        self._atualizar_cota()
        self.after(80, self._processar_fila)
        self.after(1000, self._agendar_refresh_cota)

    # =========================================================================
    # Construção da interface
    # =========================================================================
    def _construir_widgets(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=4)
        self.grid_rowconfigure(7, weight=2)

        # --- Título -------------------------------------------------------
        titulo = ctk.CTkLabel(
            self,
            text="Baixador de DANFE",
            font=ctk.CTkFont(size=22, weight="bold"),
        )
        titulo.grid(row=0, column=0, padx=24, pady=(20, 4), sticky="ew")

        subtitulo = ctk.CTkLabel(
            self,
            text="Cole as chaves de acesso (44 dígitos), uma por linha",
            font=ctk.CTkFont(size=13),
            text_color="gray",
        )
        subtitulo.grid(row=1, column=0, padx=24, pady=(0, 8), sticky="ew")

        # --- Campo de chaves ---------------------------------------------
        self.texto_chaves = ctk.CTkTextbox(
            self,
            height=140,
            font=ctk.CTkFont(family="Consolas", size=14),
        )
        self.texto_chaves.grid(
            row=2, column=0, padx=24, pady=(0, 4), sticky="nsew"
        )
        self.texto_chaves.insert("1.0", "")

        # --- Barra de progresso e contadores ------------------------------
        self.barra_progresso = ctk.CTkProgressBar(self)
        self.barra_progresso.grid(row=3, column=0, padx=24, pady=(8, 4), sticky="ew")
        self.barra_progresso.set(0)

        stats = ctk.CTkFrame(self, corner_radius=12)
        stats.grid(row=4, column=0, padx=24, pady=4, sticky="ew")
        stats.grid_columnconfigure((0, 1, 2), weight=1, uniform="stats")

        self.lbl_processadas = self._criar_stat(stats, 0, "Processadas")
        self.lbl_sucesso = self._criar_stat(stats, 1, "Sucesso")
        self.lbl_falhas = self._criar_stat(stats, 2, "Falhas")

        # --- Cota gratuita (usada / disponível / próxima janela) ---------
        cota = ctk.CTkFrame(self, corner_radius=12, fg_color="transparent")
        cota.grid(row=5, column=0, padx=24, pady=(2, 0), sticky="ew")
        self.lbl_cota = ctk.CTkLabel(
            cota,
            text="Cota — calculando...",
            anchor="w",
            font=ctk.CTkFont(size=12),
            text_color="#e0a63c",
        )
        self.lbl_cota.grid(row=0, column=0, sticky="ew")

        # --- Nota atual / status ------------------------------------------
        self.lbl_atual = ctk.CTkLabel(
            self,
            text="Aguardando consulta...",
            anchor="w",
            font=ctk.CTkFont(size=12),
        )
        self.lbl_atual.grid(row=6, column=0, padx=24, pady=(4, 0), sticky="ew")

        # --- Resumo ---------------------------------------------------------
        self.texto_resumo = ctk.CTkTextbox(
            self,
            font=ctk.CTkFont(family="Consolas", size=10),
            state="disabled",
            height=120,
        )
        self.texto_resumo.grid(row=7, column=0, padx=24, pady=(10, 4), sticky="nsew")

        # --- Botões ---------------------------------------------------------
        botoes = ctk.CTkFrame(self, corner_radius=12, fg_color="transparent")
        botoes.grid(row=8, column=0, padx=24, pady=(8, 20), sticky="ew")
        botoes.grid_columnconfigure((0, 1, 2, 3, 4), weight=1, uniform="botoes")

        self.btn_processar = ctk.CTkButton(
            botoes,
            text="Processar DANFEs",
            command=self._iniciar_processamento,
            corner_radius=14,
            height=42,
            font=ctk.CTkFont(size=13, weight="bold"),
        )
        self.btn_processar.grid(row=0, column=0, padx=4, pady=4, sticky="ew")

        self.btn_copiar = ctk.CTkButton(
            botoes,
            text="Copiar Chaves com Erro",
            command=self._copiar_chaves_erro,
            state="disabled",
            corner_radius=14,
            height=42,
            font=ctk.CTkFont(size=13, weight="bold"),
            fg_color="gray30",
            hover_color="gray25",
        )
        self.btn_copiar.grid(row=0, column=1, padx=4, pady=4, sticky="ew")

        self.btn_abrir = ctk.CTkButton(
            botoes,
            text="Abrir Downloads",
            command=lambda: downloads.abrir_pasta(downloads.pasta_downloads()),
            corner_radius=14,
            height=42,
            font=ctk.CTkFont(size=13, weight="bold"),
            fg_color="gray30",
            hover_color="gray25",
        )
        self.btn_abrir.grid(row=0, column=2, padx=4, pady=4, sticky="ew")

        self.btn_nova = ctk.CTkButton(
            botoes,
            text="Nova Consulta",
            command=self._nova_consulta,
            corner_radius=14,
            height=42,
            font=ctk.CTkFont(size=13, weight="bold"),
            fg_color="gray30",
            hover_color="gray25",
        )
        self.btn_nova.grid(row=0, column=3, padx=4, pady=4, sticky="ew")

        self.btn_fechar = ctk.CTkButton(
            botoes,
            text="Fechar",
            command=self.destroy,
            corner_radius=14,
            height=42,
            font=ctk.CTkFont(size=13, weight="bold"),
            fg_color="#b03a2e",
            hover_color="#8f2f25",
        )
        self.btn_fechar.grid(row=0, column=4, padx=4, pady=4, sticky="ew")

    def _criar_stat(self, pai: ctk.CTkFrame, coluna: int, titulo: str) -> ctk.CTkLabel:
        """Cria um card com título e valor zero para os contadores."""
        cartao = ctk.CTkFrame(pai, corner_radius=8)
        cartao.grid(row=0, column=coluna, padx=8, pady=8, sticky="nsew")
        ctk.CTkLabel(cartao, text=titulo, font=ctk.CTkFont(size=11)).pack(pady=(6, 0))
        valor = ctk.CTkLabel(cartao, text="0", font=ctk.CTkFont(size=22, weight="bold"))
        valor.pack(pady=(0, 6))
        return valor

    # =========================================================================
    # Laço de processamento
    # =========================================================================
    def _iniciar_processamento(self) -> None:
        """Lê as chaves, prepara os contadores e dispara o thread worker."""
        if self._processando:
            return

        linhas = self.texto_chaves.get("1.0", "end").splitlines()
        chaves = [
            chave.normalizar_chave(linha)
            for linha in linhas
            if chave.normalizar_chave(linha)
        ]

        if not chaves:
            self._adicionar_resumo("Nenhuma chave de acesso informada.")
            return

        self._contadores = Contadores(total=len(chaves))
        self._processando = True
        self._alternar_estado_widgets(habilitado=False)
        self._atualizar_contadores()

        worker = threading.Thread(
            target=self._worker,
            args=(chaves,),
            name="worker-danfe",
            daemon=True,
        )
        worker.start()

    def _worker(self, chaves: list[str]) -> None:
        """Processa as chaves em segundo plano e publica eventos na fila."""
        try:
            self._worker_principal(chaves)
        except Exception as exc:  # noqa: BLE001 — nunca deixar a UI travada
            self._fila.put(("finalizar", f"Erro inesperado: {exc}"))

    def _worker_principal(self, chaves: list[str]) -> None:
        pasta = downloads.pasta_downloads()
        interrompido = False

        if config.RITMO_CAUTELOSO:
            self._fila.put(("aviso", pacing.registrar_contador()))

        for idx, acesso in enumerate(chaves, start=1):
            # ---- Ritmo cauteloso: teto diário + teto por hora ------------
            if config.RITMO_CAUTELOSO:
                if pacing.cota_disponivel() <= 0:
                    interrompido = True
                    for restante in chaves[idx - 1:]:
                        self._fila.put(("resultado", {
                            "chave": restante,
                            "numero": "",
                            "status": config.STATUS_FALHA,
                            "mensagem": "Não processada: cota diária atingida.",
                        }))
                    self._fila.put((
                        "aviso",
                        "Cota diária atingida. Continue amanhã ou aumente "
                        "LIMITE_CHAVES_DIA no config.py.",
                    ))
                    break

            # Pequena pausa entre chaves para respeitar o limite por minuto
            # (~60 req/min): passar um pouco da hora é aceitável, estourar
            # o minuto não.
            if idx > 1:
                time.sleep(config.DELAY_ENTRE_CHAVES)

            self._fila.put(("atual", f"[{idx}/{len(chaves)}] Consultando chave {acesso}"))

            # ---- Validação local (sem gastar requisição) -----------------
            valido, motivo = chave.validar_chave(acesso)
            if not valido:
                self._fila.put(("resultado", {
                    "chave": acesso,
                    "numero": "",
                    "status": config.STATUS_FALHA,
                    "mensagem": motivo,
                }))
                continue

            # ---- Consulta na API -------------------------------------------------
            resposta = api_client.consultar_danfe(acesso)
            if config.RITMO_CAUTELOSO:
                pacing.somar_consumo(1)
                pacing.somar_consumo_hora(1)

            if resposta["ok"]:
                numero = resposta["numero"] or chave.numero_nota_da_chave(acesso)
                try:
                    caminho = downloads.salvar_pdf(
                        resposta["pdf_bytes"], numero, pasta
                    )
                except OSError as exc:
                    self._fila.put(("resultado", {
                        "chave": acesso,
                        "numero": numero,
                        "status": config.STATUS_FALHA,
                        "mensagem": f"Erro de gravação do arquivo: {exc}",
                    }))
                    continue
                except ValueError as exc:
                    self._fila.put(("resultado", {
                        "chave": acesso,
                        "numero": numero,
                        "status": config.STATUS_FALHA,
                        "mensagem": f"Falha no download: {exc}",
                    }))
                    continue

                self._fila.put(("resultado", {
                    "chave": acesso,
                    "numero": numero,
                    "status": config.STATUS_SUCESSO,
                    "mensagem": f"Salvo como {caminho.name}",
                    "caminho": caminho,
                }))
            else:
                self._fila.put(("resultado", {
                    "chave": acesso,
                    "numero": resposta.get("numero", ""),
                    "status": config.STATUS_FALHA,
                    "mensagem": resposta["mensagem"],
                }))

                # Limite "duro" da API (cota diária/IP): não adianta insistir
                # nas próximas chaves — interrompe o lote imediatamente.
                if resposta.get("codigo") == "rate_limit_longo":
                    interrompido = True
                    for restante in chaves[idx:]:
                        self._fila.put(("resultado", {
                            "chave": restante,
                            "numero": "",
                            "status": config.STATUS_FALHA,
                            "mensagem": "Não processada: limite da API atingido.",
                        }))
                    self._fila.put(("aviso", resposta["mensagem"]))
                    break

        # ---- Relatório Excel + resumo final ------------------------------
        try:
            caminho_relatorio = report.gerar_relatorio(
                self._contadores.resultado, pasta
            )
            mensagem_relatorio = f"Relatório gerado: {caminho_relatorio.name}"
        except OSError as exc:
            mensagem_relatorio = f"Falha ao gerar o relatório Excel: {exc}"

        if interrompido:
            mensagem_relatorio = (
                "Execução interrompida por limite da API. " + mensagem_relatorio
            )

        self._fila.put(("finalizar", mensagem_relatorio))

    def _processar_fila(self) -> None:
        """Consome os eventos do worker no thread principal (chamado via after)."""
        try:
            while True:
                evento = self._fila.get_nowait()
                tipo = evento[0]

                if tipo == "atual":
                    self.lbl_atual.configure(text=evento[1])

                elif tipo == "aviso":
                    self.lbl_atual.configure(text=evento[1])
                    self._adicionar_resumo(f"[AVISO] {evento[1]}")

                elif tipo == "resultado":
                    dados = evento[1]
                    total = self._contadores.total
                    if dados["status"] == config.STATUS_SUCESSO:
                        self._contadores.sucesso += 1
                        self._contadores.baixadas.append(
                            f"{dados['chave']} -> NF_{dados['numero']}.pdf"
                        )
                        self._adicionar_resumo(
                            f"[OK] {dados['chave']} | NF_{dados['numero']}.pdf"
                        )
                    else:
                        self._contadores.falhas += 1
                        self._contadores.falhas_chaves.append(dados["chave"])
                        self._adicionar_resumo(
                            f"[FALHA] {dados['chave']} | {dados['mensagem']}"
                        )
                    self._contadores.processadas += 1
                    self._contadores.resultado.append(dados)
                    self._atualizar_contadores()
                    self.barra_progresso.set(
                        self._contadores.processadas / max(total, 1)
                    )

                elif tipo == "finalizar":
                    self._contadores.processadas = self._contadores.total
                    self._finalizar(evento[1])

        except queue.Empty:
            pass

        self.after(80, self._processar_fila)

    # =========================================================================
    # Finalização e utilitários de interface
    # =========================================================================
    def _finalizar(self, mensagem_relatorio: str) -> None:
        """Preenche o resumo final, libera a interface e habilita os botões."""
        c = self._contadores
        self._processando = False
        self._alternar_estado_widgets(habilitado=True)
        self.barra_progresso.set(1.0 if c.total else 0.0)

        self.lbl_atual.configure(text="Concluído.")

        self._adicionar_resumo("")
        self._adicionar_resumo("=" * 62)
        self._adicionar_resumo("RESUMO DA EXECUÇÃO")
        self._adicionar_resumo(f"Total de chaves processadas: {c.total}")
        if config.RITMO_CAUTELOSO:
            self._adicionar_resumo(pacing.registrar_contador())
        self._adicionar_resumo(f"DANFEs baixadas com sucesso: {c.sucesso}")
        self._adicionar_resumo(f"Total de falhas: {c.falhas}")

        self._adicionar_resumo("")
        self._adicionar_resumo("NOTAS BAIXADAS:")
        for item in c.baixadas:
            self._adicionar_resumo(f"  • {item}")

        self._adicionar_resumo("")
        self._adicionar_resumo("CHAVES COM FALHA:")
        if c.falhas_chaves:
            for acesso in c.falhas_chaves:
                self._adicionar_resumo(f"  • {acesso}")
        else:
            self._adicionar_resumo("  • (nenhuma)")

        self._adicionar_resumo("")
        self._adicionar_resumo(mensagem_relatorio)
        self._adicionar_resumo("=" * 62)

        if c.falhas:
            self.btn_copiar.configure(state="normal")

    def _atualizar_contadores(self) -> None:
        """Atualiza os rótulos numéricos com os contadores atuais."""
        self.lbl_processadas.configure(text=str(self._contadores.processadas))
        self.lbl_sucesso.configure(text=str(self._contadores.sucesso))
        self.lbl_falhas.configure(text=str(self._contadores.falhas))
        self._atualizar_cota()

    @staticmethod
    def _formatar_tempo(segundos: float) -> str:
        """Segundos -> 'Xmin Ys' (tempo até a próxima janela)."""
        total = max(0, int(segundos))
        if total >= 3600:
            return f"{total // 3600}h {total % 3600 // 60}min"
        if total >= 60:
            return f"{total // 60}min {total % 60}s"
        return f"{total}s"

    def _atualizar_cota(self) -> None:
        """Atualiza o painel da cota gratuita: usada / disponível / tempo."""
        if not config.RITMO_CAUTELOSO:
            self.lbl_cota.configure(text="Cota desligada (RITMO_CAUTELOSO = False).")
            return
        usados = pacing.consumo_hoje()
        livres = pacing.cota_disponivel()
        hora = pacing.consumo_hora()
        limite_hora = config.LIMITE_CHAVES_HORA
        tempo = self._formatar_tempo(pacing.segundos_ate_proxima_hora())
        self.lbl_cota.configure(
            text=(
                f"Cota gratuita — usados hoje: {usados} | disponíveis hoje: "
                f"{livres} | hora: {hora}/{limite_hora} | próxima janela: "
                f"{tempo}"
            )
        )

    def _agendar_refresh_cota(self) -> None:
        """Mantém o painel da cota atualizado a cada segundo."""
        self._atualizar_cota()
        self.after(1000, self._agendar_refresh_cota)

    def _adicionar_resumo(self, texto: str) -> None:
        """Acrescenta uma linha ao painel de resumo (somente leitura)."""
        self.texto_resumo.configure(state="normal")
        self.texto_resumo.insert("end", texto + "\n")
        self.texto_resumo.see("end")
        self.texto_resumo.configure(state="disabled")

    def _alternar_estado_widgets(self, habilitado: bool) -> None:
        """Habilita/desabilita os controles conforme o estado de execução."""
        estado = "normal" if habilitado else "disabled"
        self.texto_chaves.configure(state=estado)
        self.btn_processar.configure(state=estado)
        self.btn_nova.configure(state=estado)
        if not habilitado:
            self.btn_copiar.configure(state="disabled")

    # =========================================================================
    # Ações dos botões
    # =========================================================================
    def _copiar_chaves_erro(self) -> None:
        """Copia para a área de transferência apenas as chaves que falharam."""
        texto = "\n".join(self._contadores.falhas_chaves)
        self.clipboard_clear()
        self.clipboard_append(texto)
        self.lbl_atual.configure(text=f"{len(self._contadores.falhas_chaves)} chaves com erro copiadas.")

    def _nova_consulta(self) -> None:
        """Limpa a tela permitindo nova execução sem fechar o programa."""
        self.texto_chaves.configure(state="normal")
        self.texto_chaves.delete("1.0", "end")
        self.texto_resumo.configure(state="normal")
        self.texto_resumo.delete("1.0", "end")
        self.texto_resumo.configure(state="disabled")
        self._contadores = Contadores()
        self._atualizar_contadores()
        self.barra_progresso.set(0)
        self.lbl_atual.configure(text="Aguardando consulta...")