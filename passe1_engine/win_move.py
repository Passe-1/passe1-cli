"""Bases experimentais para movimentação recuperável no Windows.

Ainda não publica operações de movimentação: o protocolo de handles e journal
deve ser implementado e validado nos runners antes da integração.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os

from passe1_engine.win_secure import LIMITE_EDICAO, _dpapi


def _digest(texto):
    if not isinstance(texto, str):
        raise TypeError('Conteúdo inválido para movimentação.')
    dados = texto.encode('utf-8')
    if len(dados) > LIMITE_EDICAO:
        raise ValueError('Conteúdo excede o limite de 64 MiB para movimentação.')
    return hashlib.sha256(dados).hexdigest()


def _estado(origem, destino, plano):
    """Identifica estados conhecidos por identidade física e hash de conteúdo.

    Uma entrada presente é um par ``(identidade, hash)`` obtido do mesmo
    handle. Uma entrada ausente é ``None``. Outros estados exigem revisão.
    """
    esperado_o = (plano['id_origem'], plano['hash_origem'])
    esperado_d = (plano['id_destino'], plano['hash_destino'])
    novo_d = (plano['id_destino'], plano['hash_novo'])

    def igual(atual, esperado):
        return (isinstance(atual, tuple) and len(atual) == 2
                and atual[0] == esperado[0]
                and isinstance(atual[1], str)
                and hmac.compare_digest(atual[1], esperado[1]))

    if igual(origem, esperado_o) and igual(destino, esperado_d):
        return 'original'
    if igual(origem, esperado_o) and igual(destino, novo_d):
        return 'destino_atualizado'
    if origem is None and igual(destino, novo_d):
        return 'aplicado'
    return 'divergente'


def _validar_journal(dados):
    if not isinstance(dados, dict) or dados.get('versao') != 1:
        raise ValueError('Journal de movimentação inválido.')
    for nome in ('origem', 'destino'):
        caminho = dados.get(nome)
        if not isinstance(caminho, str) or not os.path.isabs(caminho):
            raise ValueError('Caminho inválido no journal.')
        identidade = dados.get('id_' + nome)
        if not isinstance(identidade, list) or len(identidade) != 3 or any(not isinstance(v, str) or not v.isdecimal() for v in identidade):
            raise ValueError('Identidade inválida no journal.')
        pais = dados.get('pais_' + nome)
        if not isinstance(pais, list) or not pais or any(
            not isinstance(pai, list) or len(pai) != 3
            or any(not isinstance(v, str) or not v.isdecimal() for v in pai)
            for pai in pais
        ):
            raise ValueError('Identidade dos diretórios inválida no journal.')
    if os.path.normcase(dados['origem']) == os.path.normcase(dados['destino']) or dados['id_origem'] == dados['id_destino']:
        raise ValueError('Origem e destino não podem ser o mesmo arquivo.')
    for nome in ('origem', 'destino', 'novo'):
        texto = dados.get('conteudo_' + nome)
        digest = dados.get('hash_' + nome)
        if not isinstance(digest, str) or not hmac.compare_digest(_digest(texto), digest):
            raise ValueError('Conteúdo do journal não corresponde ao hash.')
    return dados


def _proteger_journal(dados):
    validado = _validar_journal(dados)
    serializado = json.dumps(validado, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    return _dpapi(serializado, True)


def _abrir_journal(dados):
    bruto = _dpapi(dados, False)
    try:
        conteudo = json.loads(bruto.decode('utf-8'))
    except (UnicodeError, ValueError) as exc:
        raise ValueError('Journal de movimentação inválido.') from exc
    return _validar_journal(conteudo)
