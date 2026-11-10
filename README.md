# CLI do Passe1

Agente de programação no terminal com conta Passe1, saldo pré-pago,
seleção de modelos e integração com o gateway.

## Instalação

```sh
curl -fsSL https://passe.dehor.com.br/cli/install.sh | sh

# depois use direto, de qualquer pasta:
passe1 login
passe1 agent
```

Para atualizar, execute novamente o instalador acima. A versão no PyPI pode estar desatualizada; prefira o instalador do site.

Desenvolvedores: instale com `pip install -e .` ou execute `python passe1.py ...`.

## Uso

```sh
# abrir o navegador, autorizar a conta e selecionar uma chave existente
passe1 login

# ou entrar diretamente com uma chave de API
passe1 login --key

# saldo, chaves e modelo padrão
passe1 status
passe1 status --json
passe1 keys
passe1 keys create
passe1 usage --dias 7
passe1 config model MODELO_COM_TOOLS
passe1 models

# agente interativo
passe1 agent
# /limpar inicia outro contexto; /sair encerra

# conversar (uma pergunta, stdin ou REPL; /sair encerra, /limpar zera)
passe1 chat "explique closures em 3 linhas"
passe1 chat
passe1 chat --model mimo-v2.6-flash "oi"
echo "resuma isto" | passe1 chat

# agente de programação: execute dentro do diretório do projeto
passe1 agent "investigue o bug, corrija e execute os testes"
passe1 agent --model MODELO_COM_TOOLS "explique a estrutura do projeto"

# imagem local (o modelo precisa aceitar visão)
passe1 chat --image captura.png "descreva a tela"
passe1 agent --image captura.png "corrija a interface"

# skills locais (SKILL.md do Codex ou de ~/.config/passe1/skills)
passe1 skills
passe1 chat --skill revisor "revise este texto"
# no REPL: /skill NOME ativa, /skill mostra a ativa, /skill off desativa
# (a ativa fica salva e vale para as proximas sessoes)
# /model mostra o atual, /model ID troca sem sair do chat

# modelos e status
passe1 models
passe1 status
passe1 models --json   # saida maquina para scripts
passe1 status --json
passe1 logout

# apontar um harness para o Passe1 (faz backup do config atual)
passe1 setup codex
```

Opções globais: `--base-url` (aponta para outro gateway, ex. o local) e
`--versao`. Prioridade do modelo: `--model`, `PASSE1_MODEL`, configuração
salva e, por fim, padrão do CLI. Login de conta exige HTTPS, exceto localhost. `--image` pode ser repetido e
aceita PNG, JPEG, GIF ou WebP, com até 4 MiB no total. As imagens são enviadas
ao modelo escolhido; o suporte a visão depende do modelo e do gateway.

## Testes

```sh
python -m pip install -r requirements.txt
python -m pytest tests -q   # sem rede (httpx MockTransport)
```

## Desenvolvimento e releases

Instale as dependências de `requirements.txt` e execute a suíte acima antes de enviar um pull request. O CI verifica os testes em Python 3.10 e 3.12. Uma release no GitHub gera um wheel como artefato.

O gateway que serve o instalador mantém atualmente uma cópia do CLI. Alterações neste repositório precisam ser sincronizadas com o gateway e publicadas nele para chegar pelo instalador do site. A publicação no PyPI depende da configuração do Trusted Publisher; não presuma que uma release no GitHub atualize o PyPI.

## Notas

- A sessão e a chave ficam em `~/.config/passe1`, com permissões restritas.
  Ao trocar de conta, selecione novamente a chave; a sessão é vinculada ao
  gateway usado no login. Use `passe1 logout` para encerrar a sessão e apagar
  as credenciais locais.
- O chat usa streaming (SSE); se o gateway responder JSON simples, a CLI
  entende mesmo assim.
- O REPL guarda historico em ~/.config/passe1/history (setas funcionam).
- O agente exige um modelo com suporte a chamadas de ferramentas. Ele lista
  e lê arquivos dentro do projeto, mostra o diff antes de criar ou editar
  arquivos e pede aprovação para cada alteração e comando. Comandos aprovados
  executam no shell normal, com as permissões do usuário e acesso à rede;
  revise-os antes de aceitar. O conteúdo lido é enviado ao modelo escolhido.
  Arquivos com nomes sensíveis conhecidos, como `.env` e chaves privadas, exigem
  aprovação antes da leitura e são ignorados pela busca. Essa identificação não
  encontra todos os segredos possíveis; revise seu projeto e os comandos aprovados.
- O agente mantém a conversa e as ferramentas estáveis entre chamadas e envia
  um ID de conversa ao gateway para favorecer afinidade de cache. Quando o
  modelo informa tokens reutilizados, o CLI mostra esse número; a taxa efetiva
  depende do modelo e do provedor e não é garantida.
- `setup codex` pede `export PASSE1_API_KEY=$(cat ~/.config/passe1/key)` no
  terminal.
