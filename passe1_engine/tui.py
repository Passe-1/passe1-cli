"""Interface de terminal do Passe1, com motor de execução isolado."""
from __future__ import annotations

import asyncio
import base64
import json
import os
import queue
import re
import secrets
import subprocess
import threading
import time
from pathlib import Path
from urllib.parse import quote

import httpx
from prompt_toolkit.application import Application
from prompt_toolkit.completion import Completer, Completion, PathCompleter
from prompt_toolkit.document import Document
from prompt_toolkit.filters import Condition
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.keys import Keys
from prompt_toolkit.layout import ConditionalContainer, HSplit, Layout, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.processors import AfterInput, ConditionalProcessor
from prompt_toolkit.styles import Style
from prompt_toolkit.utils import get_cwidth
from prompt_toolkit.widgets import TextArea


def _limpar(texto):
    return ''.join(c for c in str(texto) if c in '\n\t' or (ord(c) >= 32 and ord(c) != 127 and not 0x80 <= ord(c) <= 0x9f))


def _encerrar_servidor(processo):
    try:
        if processo.poll() is None:
            try:
                processo.terminate()
            except ProcessLookupError:
                pass
            try:
                processo.wait(timeout=5)
            except subprocess.TimeoutExpired:
                processo.kill()
                processo.wait()
    finally:
        if os.name == 'nt':
            from passe1_engine.win_job import fechar
            fechar(processo)


def _servidor(binario, ambiente):
    segredo = secrets.token_urlsafe(32)
    env = dict(ambiente, OPENCODE_SERVER_USERNAME='opencode', OPENCODE_SERVER_PASSWORD=segredo)
    if os.name == 'nt':
        from passe1_engine.win_job import iniciar as iniciar_processo
    else:
        iniciar_processo = subprocess.Popen
    processo = iniciar_processo(
        [str(binario), 'serve', '--hostname', '127.0.0.1', '--port', '0', '--no-mdns', '--pure'],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
    )
    linhas = queue.Queue()
    iniciado = threading.Event()
    def ler():
        try:
            for linha in processo.stdout:
                if not iniciado.is_set():
                    linhas.put(linha)
        finally:
            linhas.put(None)
    threading.Thread(target=ler, daemon=True).start()
    try:
        while True:
            linha = linhas.get(timeout=45)
            if linha is None:
                raise RuntimeError('O motor encerrou antes de iniciar.')
            encontrado = re.search(r'listening on http://127\.0\.0\.1:(\d+)', linha)
            if encontrado:
                porta = int(encontrado.group(1))
                if 0 < porta < 65536:
                    iniciado.set()
                    return processo, f'http://127.0.0.1:{porta}', segredo
    except BaseException:
        _encerrar_servidor(processo)
        raise


class ComposerCompleter(Completer):
    comandos = ('/ajuda', '/anexar', '/imagem', '/anexos', '/remover', '/modelo', '/limpar', '/sair')

    def get_completions(self, document, complete_event):
        texto = document.text_before_cursor
        if texto.startswith('/') and ' ' not in texto:
            for comando in self.comandos:
                if comando.startswith(texto):
                    yield Completion(comando, start_position=-len(texto))
            return
        prefixo = next((x for x in ('/anexar ', '/imagem ') if texto.startswith(x)), None)
        caminho = texto[len(prefixo):] if prefixo else None
        if caminho is not None:
            yield from PathCompleter(expanduser=True).get_completions(Document(caminho), complete_event)


class Interface:
    def __init__(self, cliente, modelo, sessao, imagens, modelos=None):
        self.cliente = cliente
        self.modelo = modelo
        self.modelos = modelos or (modelo,)
        self.uso_etapas = {}
        self.sessao = sessao
        self.imagens = imagens
        self.anexos = []
        self.anexo_pendente = None
        self.preparando_anexo = False
        self.historico = []
        self.indice_historico = 0
        self.rascunho = ""
        self.linhas = []
        self.partes = {}
        self.papeis = {}
        self.mostrados = set()
        self.interacoes = []
        self.respondendo = False
        self.ocupado = False
        self.conectado = asyncio.Event()
        self.descendentes = {sessao}
        self.estado = 'Conectando'
        self._largura_transcricao = None
        self._seguindo = True
        self.transcricao = TextArea(text='', read_only=True, scrollbar=False, wrap_lines=True, focusable=False, height=lambda: min(18, max(3, self.transcricao.text.count('\n') + 2)))
        self.entrada = TextArea(height=lambda: min(6, max(1, self.entrada.text.count('\n') + 1)), multiline=True, prompt='❯ ', wrap_lines=True, style='class:entrada', history=InMemoryHistory(), completer=ComposerCompleter(), complete_while_typing=False)
        self.entrada.control.input_processors.append(ConditionalProcessor(
            AfterInput('Descreva uma tarefa ou cole um arquivo', style='class:dica'),
            filter=Condition(lambda: not self.entrada.text and self.app.output.get_size().columns >= 46),
        ))
        teclas = KeyBindings()

        @teclas.add('enter')
        def enviar(event):
            if self.preparando_anexo:
                self._adicionar('\nAguarde a preparação do anexo antes de enviar.\n')
                return
            if self.entrada.text.endswith('\\'):
                self.entrada.buffer.delete_before_cursor(count=1)
                self.entrada.buffer.insert_text('\n')
                return
            texto = self.entrada.text.strip()
            if texto and (not self.historico or self.historico[-1] != texto):
                self.historico.append(texto)
            self.indice_historico = len(self.historico)
            self.entrada.buffer.append_to_history()
            self.entrada.buffer.reset()
            if texto:
                event.app.create_background_task(self.enviar(texto))

        @teclas.add(Keys.BracketedPaste)
        def colar(event):
            from passe1_engine.attachments import extrair_caminhos
            caminhos = extrair_caminhos(event.data)
            if caminhos and not self.ocupado and not self.interacoes and not self.preparando_anexo:
                self.preparando_anexo = True
                event.app.create_background_task(self._colar_caminhos(caminhos))
            else:
                event.current_buffer.insert_text(event.data.replace('\r\n', '\n').replace('\r', '\n'))

        @teclas.add('c-v')
        @teclas.add('escape', 'v')
        def clipboard(event):
            if not self.preparando_anexo:
                self.preparando_anexo = True
                event.app.create_background_task(self.colar_clipboard())

        @teclas.add('c-j')
        @teclas.add('c-o')
        def nova_linha(event):
            self.entrada.buffer.insert_text('\n')

        @teclas.add('c-c')
        def interromper(event):
            if self.ocupado:
                event.app.create_background_task(self.abortar())
            else:
                event.app.exit()

        @teclas.add('up', eager=True)
        def anterior(event):
            buffer = event.current_buffer
            if buffer.document.cursor_position_row > 0 or buffer.complete_state:
                buffer.auto_up()
            elif self.indice_historico > 0:
                if self.indice_historico == len(self.historico):
                    self.rascunho = buffer.text
                self.indice_historico -= 1
                buffer.set_document(Document(self.historico[self.indice_historico], cursor_position=0))

        @teclas.add('down', eager=True)
        def proximo(event):
            buffer = event.current_buffer
            if buffer.document.cursor_position_row < buffer.document.line_count - 1 or buffer.complete_state:
                buffer.auto_down()
            elif self.indice_historico < len(self.historico):
                self.indice_historico += 1
                texto = self.rascunho if self.indice_historico == len(self.historico) else self.historico[self.indice_historico]
                buffer.set_document(Document(texto, cursor_position=len(texto)))

        @teclas.add('pageup')
        def subir(event):
            self._seguindo = False
            buffer = self.transcricao.buffer
            buffer.cursor_up(count=max(1, event.app.output.get_size().rows - 6))

        @teclas.add('pagedown')
        def descer(event):
            buffer = self.transcricao.buffer
            buffer.cursor_down(count=max(1, event.app.output.get_size().rows - 6))
            self._seguindo = buffer.document.cursor_position_row == buffer.document.line_count - 1

        @teclas.add('c-d')
        def sair(event):
            event.app.exit()

        @teclas.add('escape')
        def cancelar(event):
            if self.ocupado:
                event.app.create_background_task(self.abortar())

        cores = 'NO_COLOR' not in os.environ and os.environ.get('TERM') != 'dumb'
        estilo = Style.from_dict({
            'marca': 'bold fg:#101e5a bg:#ffd43b' if cores else 'bold reverse',
            'titulo': 'bold fg:#ffd43b' if cores else 'bold',
            'subtitulo': 'fg:#5ee6e0' if cores else '',
            'rodape': 'fg:#5ee6e0' if cores else '',
            'entrada': 'bold',
            'borda': 'fg:#68728b' if cores else '',
            'dica': 'fg:#a0a9bc' if cores else '',
            'atividade': 'fg:#ffd43b' if cores else 'bold',
        })
        cabecalho = Window(FormattedTextControl(self._cabecalho), height=3, dont_extend_height=True)
        rodape = Window(FormattedTextControl(self._rodape), height=lambda: len(self._linhas_rodape()), dont_extend_height=True)
        atividade = Window(FormattedTextControl(self._atividade), height=1, dont_extend_height=True)
        atalhos = ConditionalContainer(
            Window(FormattedTextControl([
                ('class:subtitulo', '  /ajuda'), ('', ' comandos   '),
                ('class:subtitulo', '/anexar'), ('', ' arquivos   '),
                ('class:subtitulo', '/modelo'), ('', ' trocar modelo'),
            ]), height=1),
            filter=Condition(lambda: not self.linhas and self.app.output.get_size().columns >= 55),
        )
        layout = HSplit([cabecalho, atalhos, self.transcricao, atividade, Window(height=1, char='─', style='class:borda'), self.entrada, Window(height=1, char='─', style='class:borda'), rodape])
        self.app = Application(layout=Layout(layout, focused_element=self.entrada), key_bindings=teclas, style=estilo, full_screen=False, refresh_interval=0.1, before_render=lambda app: self._atualizar_transcricao())
        if imagens:
            self._adicionar(f'{len(imagens)} imagem(ns) serão enviadas com sua próxima mensagem.\n')

    @property
    def ocupado(self):
        return self._ocupado

    @ocupado.setter
    def ocupado(self, valor):
        if valor and not getattr(self, '_ocupado', False):
            self._inicio_atividade = time.monotonic()
        self._ocupado = valor

    def _atividade(self):
        if self.preparando_anexo:
            return FormattedText([('class:atividade', 'Preparando anexo · aguarde antes de enviar')])
        if self.anexo_pendente:
            return FormattedText([('class:atividade', 'Arquivo sensível · confirme ou cancele o envio')])
        if self.interacoes:
            return FormattedText([('class:atividade', '◆ Aguardando sua confirmação')])
        if not self.ocupado:
            quantidade = len(self.imagens) + len(self.anexos)
            return FormattedText([('class:subtitulo', f'{quantidade} anexo(s) pendente(s) · /anexos revisar · /remover N' if quantidade else '')])
        decorrido = max(0, time.monotonic() - self._inicio_atividade)
        quadros = ('·', '✦', '✧', '✦')
        quadro = quadros[int(decorrido * 8) % len(quadros)]
        return FormattedText([('class:atividade', f'{quadro} {self.estado}  {int(decorrido)}s  ·  Esc interromper')])

    def _cabecalho(self):
        largura = self.app.output.get_size().columns
        caminho = str(Path.cwd())
        disponivel = max(1, largura - 3)
        if len(caminho) > disponivel:
            caminho = '…' + caminho[-disponivel + 1:] if disponivel > 1 else '…'
        from passe1 import VERSAO

        return FormattedText([
            ('class:marca', ' P1 '), ('class:titulo', '  Passe1'),
            ('class:dica', f'  v{VERSAO}\n'),
            ('class:subtitulo', '  Modelo '), ('', f'{self.modelo}\n'),
            ('class:dica', f'  {_limpar(caminho).replace(chr(10), " ")}'),
        ])

    def _linhas_rodape(self):
        largura = max(1, self.app.output.get_size().columns - 1)
        partes = [self.estado, 'Enter enviar', 'Ctrl+O linha', 'Esc parar', 'PgUp/PgDn histórico', 'Ctrl+D sair']
        if self.uso_etapas:
            partes.insert(1, f'{sum(self.uso_etapas.values()):,} tokens'.replace(',', '.'))
        linhas = []
        atual = ''
        for parte in partes:
            candidato = atual + (' · ' if atual else '') + parte
            if atual and len(candidato) > largura:
                linhas.append(atual)
                atual = parte
            else:
                atual = candidato
        if atual:
            linhas.append(atual)
        return linhas

    def _rodape(self):
        return FormattedText([('class:rodape', '\n'.join(' ' + linha for linha in self._linhas_rodape()))])

    def _adicionar(self, texto):
        self.linhas.append(_limpar(texto))
        texto = ''.join(self.linhas)[-2_000_000:]
        self.linhas = [texto]
        self._atualizar_transcricao(forcar=True)
        self.app.invalidate()

    def _atualizar_transcricao(self, forcar=False):
        largura = max(1, self.app.output.get_size().columns - 1)
        if not forcar and largura == self._largura_transcricao:
            return
        original = ''.join(self.linhas)
        anterior_original = getattr(self, '_wrap_original', '')
        incremental = largura == self._largura_transcricao and original.startswith(anterior_original)
        if incremental:
            restante = original[len(anterior_original):]
            visual = self._wrap_visual
            colunas, tabcol = self._wrap_colunas, self._wrap_tabcol
        else:
            restante, visual, colunas, tabcol = original, '', 0, 0
        novos = []
        for caractere in restante:
            if caractere == '\n':
                novos.append('\n')
                colunas, tabcol = 0, 0
                continue
            caracteres = ' ' * (4 - tabcol % 4) if caractere == '\t' else caractere
            for item in caracteres:
                tamanho = max(0, get_cwidth(item))
                if colunas and colunas + tamanho > largura:
                    novos.append('\n')
                    colunas = 0
                novos.append(item)
                colunas += tamanho
                tabcol += tamanho
        texto = visual + ''.join(novos)
        self._wrap_original, self._wrap_visual = original, texto
        self._wrap_colunas, self._wrap_tabcol = colunas, tabcol
        self._largura_transcricao = largura
        anterior = self.transcricao.buffer.cursor_position
        cursor = len(texto) if self._seguindo else min(anterior, len(texto))
        self.transcricao.buffer.set_document(Document(texto, cursor_position=cursor), bypass_readonly=True)

    async def abortar(self):
        try:
            r = await self.cliente.post(f'/session/{self.sessao}/abort')
            r.raise_for_status()
            self.interacoes.clear()
            self._adicionar('\nInterrupção solicitada. Solicitações pendentes foram descartadas.\n')
        except httpx.HTTPError:
            self._adicionar('\nNão foi possível interromper a execução.\n')

    async def enviar(self, texto):
        if self.preparando_anexo:
            self.entrada.buffer.insert_text(texto)
            self._adicionar('\nAguarde a preparação do anexo antes de enviar.\n')
            return
        if texto in {'/sair', '/exit'}:
            self.app.exit()
            return
        if texto == '/ajuda':
            self._adicionar('\n/ajuda  comandos · /imagem CAMINHO  anexar imagem · /anexar CAMINHO  anexar arquivo · /anexos  revisar · /remover N  remover · /modelo  listar ou trocar · /limpar  limpar tela · /sair  sair\nCtrl+O insere nova linha. Esc ou Ctrl+C interrompe a execução.\n')
            return
        if texto == '/limpar':
            self.linhas.clear()
            self._adicionar('Tela limpa. O histórico da sessão foi preservado.\n')
            return
        if self.estado == 'Desconectado':
            self._adicionar('\nConexão encerrada. Reinicie o Passe1 antes de enviar.\n')
            return
        if self.interacoes:
            if self.respondendo:
                self._adicionar('\nAguarde o envio da resposta anterior.\n')
                return
            self.respondendo = True
            try:
                if self.interacoes[0][0] == 'permissao':
                    await self.responder_permissao(texto)
                else:
                    await self.responder_pergunta(texto)
            finally:
                self.respondendo = False
            return
        if self.ocupado:
            self._adicionar('\nAguarde a resposta ou pressione Esc para interromper.\n')
            return
        if self.anexo_pendente:
            token, caminho, pendente = self.anexo_pendente
            if texto == f'/confirmar-anexo {token}':
                try:
                    anexo = pendente.confirmar()
                    if self._tamanho_anexos() + anexo.tamanho > 4 * 1024 * 1024:
                        raise ValueError('Anexos ultrapassam o limite total de 4 MiB.')
                    self.anexos.append(anexo)
                    self._adicionar(f'\nAnexo confirmado: {anexo.nome}.\n')
                except (ValueError, OSError) as erro:
                    self._adicionar(f'\nAnexo recusado: {erro}\n')
                finally:
                    pendente.fechar()
                    self.anexo_pendente = None
            elif texto == '/cancelar-anexo':
                pendente.fechar()
                self.anexo_pendente = None
                self._adicionar('\nAnexo sensível descartado.\n')
            else:
                self._adicionar(f'\nConfirme envio do arquivo {caminho}: /confirmar-anexo {token}, ou /cancelar-anexo.\n')
            return
        if texto == '/modelo':
            modelos = '\n'.join(f"  {nome}{' (atual)' if nome == self.modelo else ''}" for nome in self.modelos)
            self._adicionar(f'\nModelos disponíveis\n{modelos}\nUse /modelo NOME para trocar nesta sessão.\n')
            return
        if texto.startswith('/modelo '):
            modelo = texto.split(maxsplit=1)[1].strip()
            if modelo in self.modelos:
                self.modelo = modelo
                self._adicionar(f'\nModelo desta sessão: {modelo}.\n')
            else:
                self._adicionar('\nModelo indisponível. Consulte /modelo.\n')
            return
        if texto == '/anexos':
            itens = [parte.get('filename', 'imagem') for parte in self.imagens] + [anexo.nome for anexo in self.anexos]
            self._adicionar('\n' + ('\n'.join(f'{i + 1}. {nome}' for i, nome in enumerate(itens)) if itens else 'Nenhum anexo pendente.') + '\n')
            return
        if texto.startswith('/remover '):
            try:
                indice = int(texto.split(' ', 1)[1]) - 1
                if indice < 0 or indice >= len(self.imagens) + len(self.anexos):
                    raise ValueError
                if indice < len(self.imagens):
                    self.imagens.pop(indice)
                else:
                    self.anexos.pop(indice - len(self.imagens))
                self._adicionar('\nAnexo removido.\n')
            except ValueError:
                self._adicionar('\nUse /anexos e /remover N com um número válido.\n')
            return
        if texto.startswith('/anexar '):
            await self.anexar(texto[len('/anexar '):])
            return
        if texto == '/imagem' or texto.startswith('/imagem '):
            caminho = texto[len('/imagem'):].strip()
            if len(caminho) >= 2 and caminho[0] == caminho[-1] and caminho[0] in "\"'":
                caminho = caminho[1:-1]
            if not caminho:
                self._adicionar('\nInforme o caminho: /imagem CAMINHO\n')
                return
            try:
                import passe1
                partes = passe1._imagens([caminho])
                url = partes[0]['image_url']['url']
                tamanhos = [len(base64.b64decode(item['url'].split(',', 1)[1], validate=True)) for item in self.imagens]
                tamanho = len(base64.b64decode(url.split(',', 1)[1], validate=True))
                if sum(tamanhos) + sum(x.tamanho for x in self.anexos) + tamanho > 4 * 1024 * 1024:
                    raise ValueError('As imagens ultrapassam o limite total de 4 MiB.')
                self.imagens.append({'type': 'file', 'mime': url.split(';', 1)[0].removeprefix('data:'), 'filename': Path(caminho).name, 'url': url})
                self._adicionar(f'\nImagem anexada: {Path(caminho).name}. Será enviada com a próxima mensagem.\n')
            except (ValueError, OSError, KeyError, IndexError) as erro:
                self._adicionar(f'\nNão foi possível anexar a imagem: {erro}\n')
            return
        self.ocupado = True
        self.estado = 'Pensando'
        self._adicionar(f'\nVocê  ›  {texto}\n\nPasse1  ›  ')
        partes = [{'type': 'text', 'text': texto}]
        partes.extend(self.imagens)
        partes.extend(anexo.parte for anexo in self.anexos)
        try:
            resposta = await self.cliente.post(f'/session/{self.sessao}/prompt_async', json={
                'model': {'providerID': 'passe1', 'modelID': self.modelo}, 'parts': partes,
            })
            resposta.raise_for_status()
            self.imagens = []
            self.anexos = []
        except httpx.HTTPError:
            self.ocupado = False
            self.estado = 'Erro'
            self._adicionar('\nNão foi possível enviar a mensagem ao motor.\n')
        self.app.invalidate()

    def _tamanho_anexos(self):
        return sum(x.tamanho for x in self.anexos) + sum(len(base64.b64decode(x['url'].split(',', 1)[1], validate=True)) for x in self.imagens)

    async def _colar_caminhos(self, caminhos):
        try:
            for caminho in caminhos:
                await self.anexar(caminho)
                if self.anexo_pendente:
                    break
        finally:
            self.preparando_anexo = False

    async def colar(self, texto):
        from passe1_engine.attachments import extrair_caminhos
        caminhos = extrair_caminhos(texto)
        if caminhos and not self.ocupado and not self.interacoes:
            for caminho in caminhos:
                await self.anexar(caminho)
                if self.anexo_pendente:
                    break
        else:
            self.entrada.buffer.insert_text(texto.replace('\r\n', '\n').replace('\r', '\n'))

    async def colar_clipboard(self):
        if self.ocupado or self.interacoes or self.anexo_pendente:
            self.preparando_anexo = False
            self._adicionar('\nConclua a interação atual antes de anexar.\n')
            return
        from passe1_engine.attachments import ler_clipboard
        self.preparando_anexo = True
        try:
            anexo = await asyncio.to_thread(ler_clipboard)
            if anexo is None:
                self._adicionar('\nNenhuma imagem disponível. Cole texto com o atalho do terminal ou use /anexar CAMINHO.\n')
                return
            if self._tamanho_anexos() + anexo.tamanho > 4 * 1024 * 1024:
                raise ValueError('Anexos ultrapassam o limite total de 4 MiB.')
            self.anexos.append(anexo)
            self._adicionar('\nImagem da área de transferência anexada. /anexos para revisar.\n')
        except (ValueError, OSError) as erro:
            self._adicionar(f'\nNão foi possível colar imagem: {erro}\n')
        finally:
            self.preparando_anexo = False

    async def anexar(self, caminho):
        if self.anexo_pendente:
            self._adicionar('\nConfirme ou cancele o anexo sensível pendente antes de continuar.\n')
            return
        from passe1_engine.attachments import (
            ConsentimentoNecessario,
            preparar,
            preparar_pendente,
        )
        try:
            if len(caminho) >= 2 and caminho[0] == caminho[-1] and caminho[0] in '"\'':
                caminho = caminho[1:-1]
            try:
                anexo = preparar(caminho)
            except ConsentimentoNecessario:
                pendente = preparar_pendente(caminho)
                token = secrets.token_hex(2)
                self.anexo_pendente = (token, caminho, pendente)
                self._adicionar(f'\nArquivo potencialmente sensível: {caminho}\nEnviar esse conteúdo ao modelo? Digite /confirmar-anexo {token}, ou /cancelar-anexo.\n')
                return
            if self._tamanho_anexos() + anexo.tamanho > 4 * 1024 * 1024:
                raise ValueError('Anexos ultrapassam o limite total de 4 MiB.')
            self.anexos.append(anexo)
            self._adicionar(f'\nAnexo preparado: {anexo.nome}. /anexos para revisar.\n')
        except (ValueError, OSError) as erro:
            self._adicionar(f'\nNão foi possível anexar: {erro}\n')

    async def responder_permissao(self, texto):
        respostas = {'s': 'once', 'sim': 'once', 'n': 'reject', 'não': 'reject', 'nao': 'reject'}
        resposta = respostas.get(texto.casefold())
        if not resposta:
            self._adicionar('\nDigite s para permitir uma vez ou n para negar.\n')
            return
        item = self.interacoes[0][1]
        try:
            r = await self.cliente.post(f"/permission/{item['id']}/reply", json={'reply': resposta})
            r.raise_for_status()
            self._remover_interacao('permissao', item['id'])
        except httpx.HTTPError:
            self._adicionar('\nNão foi possível responder à solicitação.\n')

    def _remover_interacao(self, tipo, identificador, sessao=None):
        for indice, (categoria, item) in enumerate(self.interacoes):
            if categoria == tipo and item.get('id') == identificador and (sessao is None or item.get('sessionID') == sessao):
                self.interacoes.pop(indice)
                if indice == 0:
                    self._adicionar('\nSolicitação encerrada.\n')
                    self.mostrar_interacao()
                return

    def mostrar_permissao(self, item):
        padroes = ', '.join(map(str, item.get('patterns', [])))
        dados = item.get('metadata', {})
        self._adicionar(f"\n\nPermissão necessária: {item.get('permission', 'ação')}\n{padroes}\n{dados}\nPermitir uma vez? Digite s ou n e pressione Enter.\n")

    def mostrar_interacao(self):
        if not self.interacoes:
            return
        tipo, item = self.interacoes[0]
        if tipo == 'permissao':
            self.mostrar_permissao(item)
            return
        for pergunta in item.get('questions', []):
            opcoes = ', '.join(str(x.get('label', '')) for x in pergunta.get('options', []))
            self._adicionar(f"\n{pergunta.get('question', '')}\n{opcoes}\nDigite sua resposta e pressione Enter.\n")

    async def responder_pergunta(self, texto):
        item = self.interacoes[0][1]
        questoes = item.get('questions', [])
        if len(questoes) != 1:
            self._adicionar('\nPergunta com várias partes: responda cada parte separada por |.\n')
        respostas = [[x.strip() for x in parte.split(',')] if questao.get('multiple') else [parte.strip()]
                     for questao, parte in zip(questoes, texto.split('|'))]
        if len(texto.split('|')) != len(questoes) or any(not p or any(not x for x in p) for p in respostas):
            self._adicionar('\nInforme uma resposta para cada pergunta.\n')
            return
        try:
            r = await self.cliente.post(f"/question/{item['id']}/reply", json={'answers': respostas})
            r.raise_for_status()
            self._remover_interacao('pergunta', item['id'])
        except httpx.HTTPError:
            self._adicionar('\nNão foi possível enviar a resposta.\n')

    async def eventos(self):
        try:
            async with self.cliente.stream('GET', '/event', timeout=None) as resposta:
                resposta.raise_for_status()
                async for linha in resposta.aiter_lines():
                    if not linha.startswith('data:'):
                        continue
                    try:
                        evento = json.loads(linha[5:].strip())
                    except ValueError:
                        continue
                    await self.evento(evento)
        except (httpx.HTTPError, OSError):
            pass
        self.estado = 'Desconectado'
        self.interacoes.clear()
        self._adicionar('\nConexão com o motor encerrada. Reinicie o Passe1.\n')
        self.app.invalidate()

    async def _e_descendente(self, sessao):
        vistos = set()
        for _ in range(16):
            if sessao in self.descendentes:
                self.descendentes.update(vistos)
                return True
            if not isinstance(sessao, str) or not re.fullmatch(r"ses_[A-Za-z0-9_-]+", sessao) or sessao in vistos:
                return False
            vistos.add(sessao)
            try:
                resposta = await self.cliente.get(f"/session/{sessao}")
                resposta.raise_for_status()
                sessao = resposta.json().get("parentID")
            except (httpx.HTTPError, ValueError, AttributeError):
                return False
        return False

    def _registrar_uso(self, parte):
        if parte.get('type') != 'step-finish' or not isinstance(parte.get('id'), str):
            return
        tokens = parte.get('tokens')
        if not isinstance(tokens, dict):
            return
        total = tokens.get('total')
        if type(total) is not int:
            valores = [tokens.get('input'), tokens.get('output')]
            if any(type(n) is not int or n < 0 for n in valores):
                return
            total = sum(valores)
        if total > 0:
            self.uso_etapas[parte['id']] = total

    async def evento(self, evento):
        tipo = evento.get('type')
        props = evento.get('properties') or {}
        if tipo == 'server.connected':
            self.conectado.set()
            self.estado = 'Pronto'
        if tipo == 'message.updated':
            info = props.get('info') or {}
            if info.get('sessionID') == self.sessao:
                self.papeis[info.get('id')] = info.get('role')
        elif tipo == 'message.part.updated':
            parte = props.get('part') or {}
            if parte.get('sessionID') != self.sessao or self.papeis.get(parte.get('messageID')) != 'assistant':
                return
            self._registrar_uso(parte)
            ident = parte.get('id')
            if not isinstance(ident, str):
                return
            if not hasattr(self, '_tipos_partes'):
                self._tipos_partes = {}
            self._tipos_partes[ident] = (parte.get('type'), parte.get('messageID'))
            if parte.get('type') == 'text':
                atual = parte.get('text') or ''
                anterior = self.partes.get(ident, '')
                if atual.startswith(anterior):
                    self._adicionar(atual[len(anterior):])
                self.partes[ident] = atual
            elif parte.get('type') == 'tool':
                estado = parte.get('state') or {}
                if estado.get('status') in {'running', 'pending'} and ident not in self.mostrados:
                    self.mostrados.add(ident)
                    self._adicionar(f"\n  Ferramenta: {parte.get('tool', 'ação')}\n")
        elif tipo == 'message.part.delta' and props.get('sessionID') == self.sessao and props.get('field') == 'text' and self.papeis.get(props.get('messageID')) == 'assistant':
            ident = props.get('partID')
            if getattr(self, '_tipos_partes', {}).get(ident) != ('text', props.get('messageID')):
                return
            delta = props.get('delta') or ''
            self.partes[ident] = self.partes.get(ident, '') + delta
            self._adicionar(delta)
        elif tipo in {'permission.asked', 'question.asked'} and await self._e_descendente(props.get('sessionID')):
            self.interacoes.append(('permissao' if tipo == 'permission.asked' else 'pergunta', props))
            if len(self.interacoes) == 1:
                self.mostrar_interacao()
        elif tipo in {'permission.replied', 'question.replied', 'question.rejected'}:
            categoria = 'permissao' if tipo == 'permission.replied' else 'pergunta'
            if isinstance(props.get('sessionID'), str):
                self._remover_interacao(categoria, props.get('requestID'), props['sessionID'])
        elif tipo == 'session.status' and props.get('sessionID') == self.sessao:
            estado = (props.get('status') or {}).get('type')
            if estado == 'idle':
                self.ocupado = False
                self.estado = 'Pronto'
                self._adicionar('\n')
            elif estado == 'retry':
                self.estado = 'Tentando novamente'
            elif estado == 'busy':
                self.ocupado = True
                self.estado = 'Trabalhando'
        elif tipo == 'session.error' and props.get('sessionID') == self.sessao:
            self.ocupado = False
            self.estado = 'Erro'
            self._adicionar('\nNão foi possível concluir a solicitação ao modelo. Confira sua conexão e tente novamente.\n')
        self.app.invalidate()


async def _executar(url, segredo, modelo, sessao, imagens, tarefa, modelos=None):
    cabecalhos = {'x-opencode-directory': quote(str(Path.cwd().resolve()), safe='')}
    async with httpx.AsyncClient(base_url=url, auth=('opencode', segredo), headers=cabecalhos, trust_env=False, timeout=30) as cliente:
        if not sessao:
            resposta = await cliente.post('/session', json={})
            resposta.raise_for_status()
            sessao = resposta.json()['id']
        interface = Interface(cliente, modelo, sessao, imagens, modelos)
        if sessao:
            resposta = await cliente.get(f'/session/{sessao}/message')
            resposta.raise_for_status()
            for mensagem in resposta.json():
                info = mensagem.get('info') or {}
                papel = info.get('role')
                if papel not in {'user', 'assistant'}:
                    continue
                if papel == 'assistant':
                    for parte in mensagem.get('parts', []):
                        if parte.get('type') == 'step-finish':
                            interface._registrar_uso(parte)
                textos = [parte.get('text', '') for parte in mensagem.get('parts', []) if parte.get('type') == 'text']
                if textos:
                    interface._adicionar(('\nVocê  ›  ' if papel == 'user' else '\nPasse1  ›  ') + '\n'.join(textos) + '\n')
        async def iniciar():
            tarefa_eventos = asyncio.create_task(interface.eventos())
            try:
                await asyncio.wait_for(interface.conectado.wait(), 30)
                if tarefa:
                    await interface.enviar(tarefa)
                await interface.app.run_async()
            finally:
                if getattr(interface, 'anexo_pendente', None):
                    interface.anexo_pendente[2].fechar()
                    interface.anexo_pendente = None
                if interface.ocupado:
                    await interface.abortar()
                tarefa_eventos.cancel()
                try:
                    await tarefa_eventos
                except asyncio.CancelledError:
                    pass
        await iniciar()
    return 0


def iniciar(binario, ambiente, modelo, sessao=None, imagens=None, tarefa=''):
    processo = None
    try:
        processo, url, segredo = _servidor(binario, ambiente)
        config = json.loads(ambiente.get('OPENCODE_CONFIG_CONTENT', '{}'))
        modelos = tuple(config.get('provider', {}).get('passe1', {}).get('models', {}))
        return asyncio.run(_executar(url, segredo, modelo, sessao, imagens or [], tarefa, modelos))
    except (httpx.HTTPError, OSError, ValueError, KeyError, EOFError, RuntimeError, queue.Empty, asyncio.TimeoutError):
        print('Não foi possível iniciar a interface do Passe1. Tente novamente.')
        return 1
    finally:
        if processo is not None:
            _encerrar_servidor(processo)
