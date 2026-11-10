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

O comando `passe1` em terminal interativo abre a interface própria do Passe1. Ela usa o motor OpenCode em segundo plano para executar o agente. Na interface, use `/ajuda` para ver comandos, `/imagem CAMINHO` para anexar uma imagem e Ctrl+O para inserir uma nova linha. O login e o saldo continuam disponíveis por `passe1 login` e `passe1 status`. O código do OpenCode é distribuído sob licença MIT, preservada em `passe1_engine/OPENCODE_LICENSE.txt`.

Para construir a partir do código-fonte, inicialize o submódulo (`git submodule update --init --recursive`), instale Bun 1.3.14 e execute `python scripts/build-engine.py` seguido de `python -m build --wheel`. O wheel inclui o motor nativo para a plataforma em que foi construído. Ele não depende de um OpenCode instalado separadamente.

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
# no macOS: bloqueia escrita e rede dos comandos aprovados
passe1 agent --sandbox
# /limpar inicia outro contexto; /sair encerra

# salvar e retomar explicitamente a mesma sessão
passe1 agent --session projeto "investigue o bug"
passe1 agent --session projeto "continue"

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
- `agent --sandbox` bloqueia escrita em qualquer local e rede para comandos aprovados no macOS; testes ou builds que precisam gravar arquivos podem falhar. Não bloqueia leitura de arquivos externos nem acesso a variáveis de ambiente; não o use como proteção de segredos. Em plataformas sem suporte, essa opção é recusada. Processos que se desvinculam do grupo podem continuar após o comando. Sem a opção, comandos aprovados não têm esse isolamento.
- O agente mantém a conversa e as ferramentas estáveis entre chamadas e envia
  um ID de conversa ao gateway para favorecer afinidade de cache. Quando o
  modelo informa tokens reutilizados, o CLI mostra esse número; a taxa efetiva
  depende do modelo e do provedor e não é garantida.
- `setup codex` pede `export PASSE1_API_KEY=$(cat ~/.config/passe1/key)` no
  terminal.

Em macOS e Linux, `agent --session` salva mensagens, saídas e imagens em `~/.config/passe1/sessions/`, com acesso restrito ao usuário. No Windows, o comando usa sessões do motor OpenCode, guardadas no estado isolado de `~/.config/passe1/opencode/`; elas não são compatíveis com as sessões do agente legado. O histórico pode conter dados sensíveis. Sessões nomeadas são vinculadas ao projeto, gateway, modelo e chave usados na criação.

`agent --sandbox` permanece disponível somente no macOS. A verificação de links reduz, mas não elimina, corridas causadas por outro processo local que altere diretórios simultaneamente.


A CI aceita o marcador `[windows-only]` em commits de desenvolvimento para executar somente os dois runners Windows. Essa execução mantém o gate da matriz completa vermelho; o commit final deve ser enviado sem o marcador para executar os seis alvos. A release continua exigindo todos os builds.

Quando um formatador automático estiver habilitado, o Passe1 preserva o conteúdo aprovado e informa que a formatação automática foi omitida se não for possível preservar a configuração do projeto com segurança. O formatador pode ser executado manualmente no terminal após aprovação. A busca informa quando atinge o limite de 10.000 arquivos candidatos.

No macOS e Linux, ao usar imagens, o CLI mantém cópias temporárias privadas até o motor encerrar e tenta removê-las ao sair. Uma interrupção abrupta do supervisor, como `SIGKILL`, pode deixar resíduos no diretório temporário do sistema; nesse caso, remova manualmente as pastas `passe1-cli-images-*` que não pertençam a processos ainda ativos.

No Windows, a edição segura recusa arquivos acima de 64 MiB e arquivos abertos para gravação por outro programa; salve e feche o editor antes de tentar novamente. A recuperação de uma edição interrompida usa `passe1 recover CAMINHO` e exige confirmação em terminal.

As credenciais locais no Windows usam permissões restritas à conta atual. Se um arquivo antigo tiver permissões compartilhadas, o CLI preserva o arquivo e informa seu caminho; remova-o e faça login novamente para criar uma credencial protegida. Se uma atualização for interrompida, execute `passe1 recover-credential CAMINHO` em um terminal e confirme a recuperação indicada pelo CLI.

Movimentações com `apply_patch` usam journal e guardam cópias em quarentena. Se uma operação for interrompida, use `passe1 recover-move CAMINHO_DO_JOURNAL` em um terminal para revisar o diff sanitizado e confirmar a recuperação. As cópias e o journal permanecem após a operação; a revisão integral recusa conteúdo acima de 1 MiB. No Windows, se a interrupção ocorrer antes do registro da identidade do arquivo temporário, o CLI preserva esse arquivo e informa seu caminho para inspeção e limpeza manual. O journal não garante proteção contra processos maliciosos do mesmo usuário, e um pseudoterminal não comprova presença humana.

O build do motor adquire automaticamente a trava cooperativa do submódulo. Para aplicar ou reverter patches manualmente, execute toda a sequência sob `python scripts/vendor_lock.py -- <comando>`; não execute operações Git diretas em `vendor/opencode` em paralelo com o build.
