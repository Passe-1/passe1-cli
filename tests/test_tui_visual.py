from unittest.mock import patch

from passe1_engine.tui import Interface


def test_atividade_anima_e_mostra_tempo_sem_alterar_estado():
    interface = Interface(None, 'modelo', 'ses_teste', [])
    with patch('passe1_engine.tui.time.monotonic', return_value=100):
        interface.ocupado = True
    interface.estado = 'Trabalhando'
    with patch('passe1_engine.tui.time.monotonic', return_value=101):
        primeiro = str(interface._atividade())
    with patch('passe1_engine.tui.time.monotonic', return_value=101.15):
        segundo = str(interface._atividade())
    assert primeiro != segundo
    assert '1s' in primeiro
    assert interface.estado == 'Trabalhando'
    interface.ocupado = False
    assert 'Trabalhando' not in str(interface._atividade())


def test_confirmacao_tem_prioridade_visual_sobre_spinner():
    interface = Interface(None, 'modelo', 'ses_teste', [])
    interface.ocupado = True
    interface.interacoes.append(('permissao', {}))
    assert 'Aguardando sua confirmação' in str(interface._atividade())


def test_resposta_longa_em_terminal_estreito_pode_ser_lida_e_redimensionada():
    from prompt_toolkit.data_structures import Size
    from prompt_toolkit.utils import get_cwidth

    interface = Interface(None, 'modelo', 'ses_teste', [])
    original = '開始 ' + ('conteúdo longo 漢字 ' * 80) + ' FIM'
    interface.linhas = [original]
    with patch.object(interface.app.output, 'get_size', return_value=Size(rows=20, columns=40)):
        interface._atualizar_transcricao(forcar=True)
        documento = interface.transcricao.buffer.document
        assert documento.line_count > 18
        assert all(sum(get_cwidth(c) for c in linha) <= 39 for linha in documento.lines)
        assert ''.join(documento.lines) == original
        interface._seguindo = False
        interface.transcricao.buffer.cursor_position = 0
        interface.transcricao.buffer.cursor_down(count=10)
        assert interface.transcricao.buffer.document.cursor_position_row == 10
        interface.transcricao.buffer.cursor_up(count=10)
        assert interface.transcricao.buffer.document.cursor_position_row == 0
    with patch.object(interface.app.output, 'get_size', return_value=Size(rows=20, columns=80)):
        interface._atualizar_transcricao()
        assert ''.join(interface.transcricao.buffer.document.lines) == original
        assert interface.transcricao.buffer.document.line_count < documento.line_count
    assert interface.linhas == [original]


def test_streaming_rewrap_processa_apenas_delta_apos_historico_longo():
    from prompt_toolkit.data_structures import Size
    from prompt_toolkit.utils import get_cwidth

    interface = Interface(None, 'modelo', 'ses_teste', [])
    interface.linhas = ['histórico ' * 70000]
    with patch.object(interface.app.output, 'get_size', return_value=Size(rows=24, columns=80)):
        interface._atualizar_transcricao(forcar=True)
        with patch('passe1_engine.tui.get_cwidth', wraps=get_cwidth) as medir:
            interface._adicionar('ABC')
            assert medir.call_count == 3
        assert interface.transcricao.text.endswith('ABC')
        interface.linhas = ['novo']
        interface._atualizar_transcricao(forcar=True)
        assert interface.transcricao.text == 'novo'
