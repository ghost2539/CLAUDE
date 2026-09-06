# EBS Forms (RPA) — guia operacional

Robô que consulta a tela **Localizar Ativos** do Oracle E-Business Suite do
mesmo jeito que uma pessoa faz: entra pelo SSO, abre a responsabilidade
`RENNER_FA_CONSULTA`, clica em "Informações Financeiras" e opera o cliente
Oracle Forms por teclado. Tudo em segundo plano, numa tela virtual, dentro do
processo do portal.

> Código: `integracoes/ebs_forms.py` (SSO, JVM, roteiros),
> `integracoes/ebs_forms_java/LancadorForms.java` (JVM que hospeda o Forms),
> `db/ebs_forms.py` (banco isolado `data/db/ebs_forms.db`),
> `routers/ebs_forms.py` (`/api/ebs-forms/*`),
> `static/ebs-forms/` (tela de administração: status, execuções, roteiros),
> `scripts/ebs_forms_preparar.sh` (preparação e teste).

---

## 1. Por que existe

A API REST do EBS que o portal usa devolve **PO/NF vazios**, e a base Oracle
não está ao nosso alcance. O único caminho que funciona hoje para saber a
ordem de compra e a nota fiscal de um ativo é a tela do Forms. Em vez de
alguém abrir o EBS a cada consulta, o robô faz isso sob demanda e guarda o
resultado.

Como funciona, em quatro peças:

1. **Sessão HTTP** — faz o login no SSO (Oracle Access Manager, o mesmo do
   ServiceNow) e pede o `frmservlet.jnlp` da função. O jnlp carrega tickets
   de vida curta: é obtido na hora, gravado com permissão 600 e apagado ao
   fim da sessão.
2. **Tela virtual** — um `Xvfb` no display `:99`, iniciado pelo próprio
   módulo quando não existe (ou reaproveitado se já houver um).
3. **LancadorForms** — nossa JVM. O OpenJDK 21 não tem Java Web Start, então
   este programa lê o jnlp, baixa os jars para `data/ebs_forms/jars/`,
   instancia o applet do Forms numa janela e oferece, via `java.awt.Robot`,
   teclado, área de transferência e captura de tela. Conversa com o Python
   por linhas (`tecla`, `texto`, `copiar`, `foto`, `esperar`…), respondendo
   `OK`, `ERRO` ou `EVENTO`.
4. **Roteiros** — sequências de teclas guardadas no banco, editáveis pela
   API sem reiniciar o serviço. Tela de Forms muda de ordem de campo, de
   atalho, de tempo de resposta; isso é dado, não código.

Uma consulta tenta cada Livro de `EBS_FORMS_LIVROS` na ordem (`FA_RENNER` e,
se nada vier, `FA_RENNER_FIS`) e guarda o que achou em `forms_ativo`; uma
nova consulta do mesmo critério nas 24 h seguintes responde do banco, sem
abrir o Forms (`forcar: true` ignora isso).

---

## 2. Pré-requisitos no servidor

Só a instalação de pacotes exige root. Tudo o mais (compilar, rodar, testar)
é feito pelo usuário do portal.

Pacotes, para o administrador (RHEL/Rocky/Alma):

```bash
sudo dnf install java-21-openjdk java-21-openjdk-devel   # java + javac
sudo dnf install xorg-x11-server-Xvfb                    # tela virtual
sudo dnf install fontconfig dejavu-sans-fonts            # sem fonte o Java não desenha texto
sudo dnf install libXtst libXrender libXi freetype       # Robot (XTEST), desenho e fontes
```

Se não for possível instalar o `-devel`, compile em outra máquina com o
mesmo JDK e copie `data/ebs_forms/bin/` para o servidor.

Rede: o servidor precisa alcançar `http://ebscorporativo.lojasrenner.com.br`
(home do EBS e os jars em `/OA_JAVA/`). O script de preparação confere isso.

Credenciais: uma conta de rede com a responsabilidade `RENNER_FA_CONSULTA`
liberada no EBS. Vai **só para o cofre** — nunca para o `environment`.

---

## 3. Primeira execução, passo a passo

```bash
cd ~/portal-spare          # raiz do portal (onde está main.py)

# 1) credenciais do robô no cofre
python3 scripts/cofre.py definir EBS_FORMS_USER
python3 scripts/cofre.py definir EBS_FORMS_PASS
python3 scripts/cofre.py conferir

# 2) environment (~/.config/portal-spare/environment): bloco "EBS Forms (RPA)"
#    do deploy/environment.modelo. O que importa preencher é EBS_FORMS_FUNCAO_URL
#    (seção 4); o resto já vem com padrão.

# 3) conferir pacotes, rede, cofre e compilar o lançador
bash scripts/ebs_forms_preparar.sh

# 4) teste de abertura: SSO → jnlp → Forms na tela virtual → captura
bash scripts/ebs_forms_preparar.sh testar

# 5) olhar o que o Forms mostrou
ls -t data/ebs_forms/capturas/ | head
```

O passo 3 lista cada pendência com o comando `dnf` correspondente; repita
até sair "Tudo pronto para o RPA". O passo 4 imprime o log da execução
(cada etapa do SSO, o tamanho do jnlp, os jars baixados, as janelas abertas)
e termina com o nome do arquivo `jvm-*.log`. A captura `*-tela_inicial.png`
deve mostrar a tela **Localizar Ativos**; se mostrar outra coisa (tela de
login, escolha de responsabilidade, mensagem de erro do Forms), é ela que
diz o que ajustar.

Depois de o teste passar, reinicie o portal (`deploy/portal.sh`) para que o
módulo suba com as variáveis novas, e repita o teste pela API
(`POST /api/ebs-forms/testar-abertura`) para confirmar que funciona dentro
do serviço.

---

## 4. Como obter `EBS_FORMS_FUNCAO_URL`

Sem essa variável o módulo tenta achar, na home do EBS, um link cujo texto
seja "Informações Financeiras". Funciona, mas depende de a home vir com o
menu daquela responsabilidade já expandido. Com a URL, o caminho é direto.

1. No navegador, entre no EBS e abra a responsabilidade
   `RENNER_FA_CONSULTA`.
2. Localize a função **Informações Financeiras** no menu. Clique com o botão
   direito → *Copiar link* (ou abra as ferramentas do desenvolvedor e pegue
   a requisição que dispara o download do `frmservlet.jnlp`).
3. O link costuma ter esta forma:

   ```
   http://ebscorporativo.lojasrenner.com.br/OA_HTML/RF.jsp?function_id=NNNN&resp_id=NNNN&resp_appl_id=NNN&security_group_id=0&lang_code=PTB
   ```

4. Cole em `EBS_FORMS_FUNCAO_URL=` no `environment` (sem aspas, sem
   `${}`), reinicie o portal e rode o teste de abertura.

Se a URL devolver uma página HTML em vez do jnlp, o módulo guarda a resposta
em `data/ebs_forms/depuracao/funcao_nao_jnlp.html` e ainda tenta seguir um
link `frmservlet`/`.jnlp` dentro dela. Se mesmo assim falhar, abra esse HTML
e veja o que o EBS respondeu (normalmente: função ou responsabilidade não
liberada para a conta do robô).

---

## 5. Roteiros

Um roteiro é uma lista de passos `{"acao": ..., "arg": ..., "nome": ...}`,
executada em ordem dentro da mesma sessão do Forms. Ficam na tabela
`forms_roteiro`; os padrão de `ROTEIROS_PADRAO` entram só quando o nome não
existe, então o que você ajustar é preservado entre atualizações.

| Ação | Argumento | O que faz |
|---|---|---|
| `esperar` | milissegundos | Pausa (o Forms demora a responder; sem pausa a tecla seguinte se perde) |
| `tecla` | combos separados por espaço | Ex.: `TAB`, `CTRL+F11`, `ALT+L`, `TAB TAB ENTER`. Modificadores `CTRL`, `SHIFT`, `ALT`; nomes `ENTER TAB ESC HOME END UP DOWN LEFT RIGHT PGUP PGDN SPACE BACKSPACE DELETE`, `F1`…`F12`, letras/dígitos e qualquer `VK_*` do Java pelo nome (ex.: `MINUS`) |
| `texto` | valor | Cola o texto (área de transferência + Ctrl+V). Aceita acento |
| `digitar` | valor | Digita tecla a tecla (só ASCII); dispara as validações do campo como uma pessoa faria |
| `copiar` | — | Seleciona o campo atual (Home, Shift+End, Ctrl+C) e guarda o valor em `resultado[nome]` |
| `foto` | — | Captura a tela inteira em `data/ebs_forms/capturas/<data>-<nome>.png` e anexa à execução |
| `se_vazio` | nome de um resultado | Se `resultado[nome]` **tiver** valor, pula os passos até o próximo `fim_se` |
| `fim_se` | — | Fecha o bloco do `se_vazio` |
| `documento` | — | Baixa o arquivo que o Forms mandou abrir no navegador (Arquivo → Exportar) e guarda o texto em `resultado[nome]` |

Variáveis: `{criterio}` (número do ativo, etiqueta ou série informado) e
`{livro}` (o Livro da tentativa atual). Podem aparecer em qualquer `arg`.

Roteiros que a consulta usa, nesta ordem:

| Nome | Quando roda | Padrão |
|---|---|---|
| `abrir` | logo após a JVM ficar pronta | espera 20 s e fotografa (`tela_inicial`) |
| `localizar_ativo` | uma vez por Livro | cola `{criterio}`, `TAB`, `ALT+L`, espera 4 s, fotografa |
| `ler_ativo` | logo depois, no mesmo Livro | `copiar` + `TAB` pelos campos: nr_ativo, descricao, etiqueta, categoria, serie, chave, tipo_ativo, unidades, tipo_propriedade |
| `voltar_localizar` | só se existir, quando um Livro não devolveu nada | — (não há padrão; crie se a tela precisar de `ESC`/`F11` para voltar ao critério) |
| `linhas_origem` | só se existir e ativo, depois de encontrar o ativo | `ALT+L`, espera 3 s, fotografa |

O ativo é considerado **encontrado** quando algum campo de `ler_ativo` vem
com valor; aí o Livro atual é gravado e os demais não são tentados.

**Como ajustar.** Os padrão são um ponto de partida: ninguém sabe de antemão
em qual campo o cursor cai nem se `ALT+L` é o atalho do botão *Localizar*
nesta versão da tela. O método é:

1. Rode uma consulta e abra as capturas da execução
   (`GET /api/ebs-forms/execucoes/{id}` lista os nomes;
   `GET /api/ebs-forms/capturas/{nome}` devolve o PNG).
2. Compare a captura `antes` com `criterio_preenchido`: o texto caiu no campo
   certo? Se não, acrescente `tecla TAB` (ou `SHIFT+TAB`) antes do `texto`.
3. Compare `resultado`: a consulta rodou? Se não, troque o atalho por
   `CTRL+F11` (executar consulta no Forms) ou pelo mnemônico que a tela
   mostrar sublinhado.
4. Se algum campo lido veio trocado, reordene os `copiar` de `ler_ativo`.
5. Salve com `PUT /api/ebs-forms/roteiros/{nome}` e repita. Para desfazer,
   `POST /api/ebs-forms/roteiros/{nome}/restaurar` volta ao padrão.

Coloque um `foto` depois de cada passo em que estiver na dúvida; tirar a
foto custa pouco e é a única janela para uma tela que ninguém vê.

---

## 6. APIs

A tela de operação fica em **`/ebs-forms`** (servida pelo router: sem sessão
redireciona ao login do portal; com sessão e sem `ebs_forms:view`, página de
acesso não liberado). Os arquivos `static/ebs-forms/` (`index.html`, `app.js`,
`app.css`) não fazem nada por conta própria: só chamam estas APIs, com a
sessão do portal. Tudo abaixo também pode ser feito direto pela API.

Prefixo `/api/ebs-forms`, módulo de permissão `ebs_forms`. Toda rodada do
robô é **assíncrona**: a chamada devolve `execucao_id` e o acompanhamento é
por `/execucoes/{id}`.

| Método e rota | Permissão | O que faz |
|---|---|---|
| `GET /status` | view | Diagnóstico: java/javac encontrados, lançador compilado, Xvfb e display, credenciais no cofre (só o usuário, nunca a senha), URLs, livros, se há sessão em andamento (`ocupado`), contagens do banco |
| `POST /compilar` | admin | Compila o `LancadorForms` (precisa de `javac`) |
| `POST /testar-abertura` | admin | Entra, abre o Forms, roda o roteiro `abrir`, fecha. Devolve `{execucao_id}` |
| `POST /consultar` | create | Corpo `{"criterio": "...", "livros": [...]?, "forcar": false}`. Se já houver coleta nas últimas 24 h e `forcar` for falso, responde na hora com `cache: true` e os dados; senão `{execucao_id, cache: false}` |
| `GET /execucoes?limite=50` | view | Últimas execuções (sem log) |
| `GET /execucoes/{id}` | view | Execução completa: situação (`rodando`/`ok`/`erro`), log passo a passo, resultado, capturas, erro |
| `GET /capturas/{nome}.png` | view | A imagem de uma captura |
| `GET /ativos/{criterio}?validade_horas=720` | view | Último dado coletado para o critério, se houver |
| `GET /roteiros` | view | Roteiros, ações válidas e variáveis |
| `PUT /roteiros/{nome}` | admin | Salva `{"descricao", "passos", "ativo"}`; nome em `[a-z0-9_]`, ações validadas |
| `POST /roteiros/{nome}/restaurar` | admin | Volta ao padrão do código |

Exemplo de uma consulta pela linha de comando, com a sessão do portal:

```bash
curl -sb cookies.txt -H 'Content-Type: application/json' \
  -d '{"criterio":"123456"}' https://portal:8901/api/ebs-forms/consultar
# → {"execucao_id": 17, "cache": false}
curl -sb cookies.txt https://portal:8901/api/ebs-forms/execucoes/17
```

O resultado de uma consulta traz `criterio`, `livro` (o que respondeu),
`encontrado`, `dados` (os campos de `ler_ativo` e, se o roteiro existir,
`linhas_origem`), `capturas`, `eventos` (as últimas 30 mensagens do Forms:
jars baixados, status, URLs de documento) e `log_jvm`.

---

## 7. Onde ficam logs, capturas e depuração

Tudo em `data/ebs_forms/` (permissão 700):

| Pasta / arquivo | Conteúdo |
|---|---|
| `bin/` | Classes compiladas do `LancadorForms` |
| `jars/` | Cache dos jars do Forms baixados do EBS (apague para forçar novo download) |
| `capturas/*.png` | Fotos da tela, nomeadas `<data>-<nome do passo>.png` |
| `logs/jvm-<data>.log` | stderr da JVM de cada sessão: tudo o que o applet do Forms imprime, exceções Java |
| `logs/xvfb.log` | Saída do Xvfb |
| `logs/javac.log` | Erros de compilação do script de preparação |
| `depuracao/sso_falha.html` | Página onde o SSO parou quando não chegou ao EBS |
| `depuracao/home.html` | Home do EBS, gravada quando o módulo procura o link da função por nome |
| `depuracao/funcao_nao_jnlp.html` | O que `EBS_FORMS_FUNCAO_URL` devolveu quando não era um jnlp |
| `depuracao/documento-<data>.txt` | Arquivos baixados pela ação `documento` |
| `sessao-<pid>-<ts>.jnlp` | jnlp da sessão em andamento (apagado ao encerrar; se sobrar, a sessão morreu à força) |

O log passo a passo de cada execução (SSO, jnlp, JVM, cada passo do roteiro)
está no banco, coluna `log` de `forms_execucao`, e sai em
`GET /execucoes/{id}`. A depuração começa por ele; os arquivos acima entram
quando o log aponta para lá.

---

## 8. Limitações conhecidas

- **Uma sessão por vez.** É um usuário de verdade logado no EBS e um só
  teclado virtual; uma trava no processo garante isso. Consulta disparada
  enquanto outra roda falha com "Já existe uma sessão Forms em andamento"
  — o `ocupado` do `/status` mostra o estado. Enfileirar é papel de quem
  chama.
- **Tempo de abertura.** Cada consulta abre uma sessão nova: SSO, download
  do jnlp, JVM subir, jars carregarem, Forms conectar. Conte de 20 a 60 s
  antes do primeiro passo útil (na primeira vez mais, pelo download dos
  jars). Por isso a API é assíncrona e por isso o cache de 24 h.
- **A tela pode mudar.** Ordem de campos, atalhos e tempos de resposta são
  da tela do Forms, não nossos. Quando um resultado vier trocado ou vazio
  depois de uma mudança no EBS, o ajuste é no roteiro (seção 5), não no
  código.
- **Leitura por teclado.** `copiar` lê o campo onde o cursor está. Campos
  somente-leitura que não aceitam foco, ou grades onde `TAB` muda de linha,
  exigem outro caminho (Arquivo → Exportar com a ação `documento`).
- **`java.applet`** está marcado para remoção no JDK. Existe no 21; se um
  dia sumir, o cliente Forms deixa de rodar em qualquer lugar, não só aqui.

---

## 9. Como diagnosticar

Comece sempre por `GET /api/ebs-forms/status` e pelo `log` da execução.
Depois, pelo sintoma:

| Sintoma | Onde olhar | Causa provável |
|---|---|---|
| "Credenciais do robô ausentes" | `python3 scripts/cofre.py conferir` | `EBS_FORMS_USER`/`EBS_FORMS_PASS` não definidos no cofre |
| "SSO não levou ao EBS" | `depuracao/sso_falha.html` | Senha errada/expirada, conta bloqueada, SSO fora do ar. Abra o HTML: a mensagem do OAM está lá |
| "Não achei o link da função na home" | `depuracao/home.html` | A home veio sem o menu da responsabilidade. Defina `EBS_FORMS_FUNCAO_URL` (seção 4) |
| "EBS_FORMS_FUNCAO_URL não devolveu um jnlp" | `depuracao/funcao_nao_jnlp.html` | URL errada ou função não liberada para a conta |
| "Xvfb não está instalado" / "Xvfb encerrou ao iniciar" | `logs/xvfb.log` | Pacote ausente, ou display `:99` em uso por outro usuário (troque `EBS_FORMS_DISPLAY`) |
| "javac não encontrado" | — | Instale `java-21-openjdk-devel` ou copie `bin/` de outra máquina |
| "JVM encerrou (código N)" | `logs/jvm-*.log` (a cauda já vem na mensagem) | Falta de biblioteca X (`libXtst`, `libXrender`, `libXi`), falta de fonte, jar corrompido no cache (`jars/`), classe do applet diferente (`EBS_FORMS_CLASSE`) |
| "Lançador não respondeu em 180s" | `logs/jvm-*.log` | Download dos jars lento ou travado (rede/proxy). Aumente `EBS_FORMS_ESPERA_JVM` na primeira vez |
| Captura mostra tela em branco | `logs/jvm-*.log` | Sem fontes (`fontconfig`, `dejavu-sans-fonts`) ou o Forms ainda carregando: aumente o `esperar` do roteiro `abrir` |
| Captura mostra outra tela (login, responsabilidade, erro FRM-) | a própria captura | O jnlp abriu outra função, ou a sessão do Forms expirou. Confira `EBS_FORMS_FUNCAO_URL` e a responsabilidade da conta |
| Consulta "ok" mas `encontrado: false` | capturas `criterio_preenchido` e `resultado` | O critério caiu no campo errado ou o atalho de Localizar não é `ALT+L` — ajuste o roteiro |
| Campos lidos trocados | captura `lido` | Ordem dos `copiar` em `ler_ativo` diferente da ordem dos campos na tela |

Para reproduzir fora do serviço, com a saída na hora:

```bash
bash scripts/ebs_forms_preparar.sh testar
```

Ele usa o mesmo código do portal (`testar_abertura`), o mesmo cofre e o
mesmo `environment`, e imprime cada etapa no terminal.
