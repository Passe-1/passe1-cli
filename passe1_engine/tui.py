"""Interface de terminal do Passe1, com motor de execução isolado."""
from __future__ import annotations

import asyncio
import base64
import os
import queue
import re
import secrets
import subprocess
import threading
from pathlib import Path
from urllib.parse import quote

import httpx
from prompt_toolkit.application import Application
from prompt_toolkit.document import Document
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import HSplit, Layout, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.styles import Style
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


class Interface:
    def __init__(self, cliente, modelo, sessao, imagens):
        self.cliente = cliente
        self.modelo = modelo
        self.sessao = sessao
        self.imagens = imagens
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
        self.transcricao = TextArea(text='', read_only=True, scrollbar=True, wrap_lines=True, focusable=False)
        self.entrada = TextArea(height=3, multiline=True, prompt='› ', wrap_lines=True)
        teclas = KeyBindings()

        @teclas.add('enter')
        def enviar(event):
            texto = self.entrada.text.strip()
            self.entrada.text = ''
            if texto:
                event.app.create_background_task(self.enviar(texto))

        @teclas.add('c-o')
        def nova_linha(event):
            self.entrada.buffer.insert_text('\n')

        @teclas.add('c-c')
        def interromper(event):
            if self.ocupado:
                event.app.create_background_task(self.abortar())
            else:
                event.app.exit()

        @teclas.add('pageup')
        def subir(event):
            buffer = self.transcricao.buffer
            buffer.cursor_up(count=max(1, event.app.output.get_size().rows - 6))

        @teclas.add('pagedown')
        def descer(event):
            buffer = self.transcricao.buffer
            buffer.cursor_down(count=max(1, event.app.output.get_size().rows - 6))

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
        })
        cabecalho = Window(FormattedTextControl(self._cabecalho), height=2, dont_extend_height=True)
        rodape = Window(FormattedTextControl(self._rodape), height=lambda: len(self._linhas_rodape()), dont_extend_height=True)
        layout = HSplit([cabecalho, self.transcricao, Window(height=1, char='─'), self.entrada, rodape])
        self.app = Application(layout=Layout(layout, focused_element=self.entrada), key_bindings=teclas, style=estilo, full_screen=True)
        self._adicionar('Bem-vindo ao Passe1. Descreva o que deseja fazer no projeto.\n')
        if imagens:
            self._adicionar(f'{len(imagens)} imagem(ns) serão enviadas com sua próxima mensagem.\n')

    def _cabecalho(self):
        largura = self.app.output.get_size().columns
        caminho = str(Path.cwd())
        disponivel = max(8, largura - len(self.modelo) - 24)
        if len(caminho) > disponivel:
            caminho = '…' + caminho[-disponivel + 1:]
        return FormattedText([('class:marca', ' P1 '), ('class:titulo', '  Passe1'), ('', f'  {self.modelo}\n'), ('class:subtitulo', f' {caminho}')])

    def _linhas_rodape(self):
        largura = max(1, self.app.output.get_size().columns - 1)
        partes = [self.estado, 'Enter enviar', 'Ctrl+O linha', 'Esc parar', 'Ctrl+D sair']
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
        self.transcricao.buffer.set_document(Document(texto, cursor_position=len(texto)), bypass_readonly=True)
        self.app.invalidate()

    async def abortar(self):
        try:
            r = await self.cliente.post(f'/session/{self.sessao}/abort')
            r.raise_for_status()
            self.interacoes.clear()
            self._adicionar('\nInterrupção solicitada. Solicitações pendentes foram descartadas.\n')
        except httpx.HTTPError:
            self._adicionar('\nNão foi possível interromper a execução.\n')

    async def enviar(self, texto):
        if texto in {'/sair', '/exit'}:
            self.app.exit()
            return
        if texto == '/ajuda':
            self._adicionar('\n/ajuda  comandos · /imagem CAMINHO  anexar imagem · /limpar  limpar tela · /sair  sair\nCtrl+O insere nova linha. Esc ou Ctrl+C interrompe a execução.\n')
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
                if sum(tamanhos) + tamanho > 4 * 1024 * 1024:
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
        try:
            resposta = await self.cliente.post(f'/session/{self.sessao}/prompt_async', json={
                'model': {'providerID': 'passe1', 'modelID': self.modelo}, 'parts': partes,
            })
            resposta.raise_for_status()
            self.imagens = []
        except httpx.HTTPError:
            self.ocupado = False
            self.estado = 'Erro'
            self._adicionar('\nNão foi possível enviar a mensagem ao motor.\n')
        self.app.invalidate()

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
                    import json
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
            ident = parte.get('id')
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


async def _executar(url, segredo, modelo, sessao, imagens, tarefa):
    cabecalhos = {'x-opencode-directory': quote(str(Path.cwd().resolve()), safe='')}
    async with httpx.AsyncClient(base_url=url, auth=('opencode', segredo), headers=cabecalhos, trust_env=False, timeout=30) as cliente:
        if not sessao:
            resposta = await cliente.post('/session', json={})
            resposta.raise_for_status()
            sessao = resposta.json()['id']
        interface = Interface(cliente, modelo, sessao, imagens)
        if sessao:
            resposta = await cliente.get(f'/session/{sessao}/message')
            resposta.raise_for_status()
            for mensagem in resposta.json():
                info = mensagem.get('info') or {}
                papel = info.get('role')
                if papel not in {'user', 'assistant'}:
                    continue
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
        return asyncio.run(_executar(url, segredo, modelo, sessao, imagens or [], tarefa))
    except (httpx.HTTPError, OSError, ValueError, KeyError, EOFError, RuntimeError, queue.Empty, asyncio.TimeoutError):
        print('Não foi possível iniciar a interface do Passe1. Tente novamente.')
        return 1
    finally:
        if processo is not None:
            _encerrar_servidor(processo)
