from __future__ import annotations
import os
from pathlib import Path
from functools import lru_cache

_ROOT = Path(__file__).parent


def _env(nome: str, padrao: str = "") -> str:
    """Variável de ambiente com `@cofre:NOME@` já trocado pelo segredo.

    É o que deixa o `environment` legível e sem senha:
    DATABASE_URL=postgresql+psycopg://portal:@cofre:DB_SENHA@@host:5432/base
    """
    v = os.getenv(nome, padrao)
    if v and "@cofre:" in v:
        from core.cofre import expandir  # tardio: o cofre não depende do config
        v = expandir(v)
    return v


def _sqlite(nome: str) -> str:
    """URL do banco SQLite `nome`, guardado em `data/db/`.

    Instalações anteriores à reorganização mantêm o arquivo em `data/`. Se o
    arquivo antigo existir e o novo ainda não, ele continua sendo usado — uma
    atualização nunca aponta o serviço para um banco vazio. Basta mover o
    arquivo para `data/db/` (com o serviço parado) para adotar o novo lugar.
    """
    novo = _ROOT / "data" / "db" / f"{nome}.db"
    antigo = _ROOT / "data" / f"{nome}.db"
    escolhido = antigo if (antigo.exists() and not novo.exists()) else novo
    try:
        escolhido.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return f"sqlite:///{escolhido.as_posix()}"


class Settings:
    ROOT: Path = _ROOT
    # Todo dado gravado em disco mora sob data/: bancos em data/db/ e
    # arquivos enviados pelos usuários em data/uploads/.
    DATA: Path = _ROOT / "data"
    DB_DIR: Path = _ROOT / "data" / "db"
    STATIC: Path = ROOT / "static"
    UPLOAD: Path = ROOT / "data" / "uploads"

    DATABASE_URL: str = _env("DATABASE_URL", os.environ["DATABASE_URL"])
    SESSION_SECRET: str = _env("PORTAL_SESSION_SECRET", os.environ["PORTAL_SESSION_SECRET"])
    # Ociosidade: a sessão cai depois deste tempo SEM uso. Cada pedido
    # autenticado reinicia a contagem — antes o prazo corria desde o login
    # e derrubava quem estava trabalhando.
    SESSION_TTL: int = int(os.getenv("SESSION_TTL_MINUTES", "480")) * 60
    # Teto absoluto: por mais que se use, a sessão não passa disto. É o que
    # impede uma aba esquecida (ou um cookie roubado) de valer para sempre.
    SESSION_MAX: int = int(os.getenv("SESSION_MAX_HOURS", "24")) * 3600

    EBS_LOGIN_URL: str = os.getenv("EBS_LOGIN_URL", "")
    EBS_SEARCH_URL: str = os.getenv("EBS_SEARCH_URL", "")
    # TLS verificado por padrão em toda saída (OAM, ServiceNow, EBS, MDM,
    # Correios). VERIFY_SSL=false é exceção explícita e fica em log. Com
    # proxy que intercepta o TLS, PORTAL_CA_BUNDLE aponta a cadeia
    # corporativa e a verificação segue ligada.
    VERIFY_SSL: bool = os.getenv("VERIFY_SSL", "true").strip().lower() not in ("false", "0", "nao", "não", "off")
    CA_BUNDLE: str = (os.getenv("PORTAL_CA_BUNDLE", "") or os.getenv("REQUESTS_CA_BUNDLE", "")).strip()
    TIMEOUT: int = int(os.getenv("TIMEOUT_SECONDS", "15"))
    MAX_WORKERS: int = int(os.getenv("MAX_WORKERS", "40"))
    CREDENTIALS_DIRECTORY: str = os.getenv("CREDENTIALS_DIRECTORY", "")

    # AMBIENTE=testes numa instância que roda ao lado da produção com cópia
    # dos bancos: desliga o que age no mundo sozinho (e-mails de alerta) e
    # marca o nome da aplicação.
    AMBIENTE: str = os.getenv("AMBIENTE", "producao").strip().lower()
    TESTES: bool = AMBIENTE in ("testes", "teste", "homologacao", "homolog")

    HOST: str = os.getenv("HOST", "0.0.0.0")
    PORT: int = int(os.getenv("PORT", "8901"))
    WORKERS: int = int(os.getenv("WORKERS", "1"))

    DEFAULT_HOURLY_RATE: float = float(os.getenv("DEFAULT_VALOR_HORA", "150"))
    INITIAL_ADMIN_LOGIN: str = os.getenv("INITIAL_ADMIN_LOGIN", "")
    # Admin geral: o único que altera identidade do portal (ícone). Em
    # branco, vale o INITIAL_ADMIN_LOGIN.
    ADMIN_GERAL_LOGIN: str = os.getenv("ADMIN_GERAL_LOGIN", "")
    UPLOAD_MAX_MB: int = int(os.getenv("UPLOAD_MAX_MB", "50"))

    RATE_LIMIT_LOGIN: str = os.getenv("RATE_LIMIT_LOGIN", "5/minute")
    RATE_LIMIT_API: str = os.getenv("RATE_LIMIT_API", "120/minute")

    SSL_CERTFILE: str = os.getenv("SSL_CERTFILE", "")
    SSL_KEYFILE: str = os.getenv("SSL_KEYFILE", "")
    # Cookie de sessão só em HTTPS: "auto" liga quando o pedido chegou por
    # https (TLS no uvicorn ou X-Forwarded-Proto de um proxy confiável);
    # "true"/"false" fixam. Atrás de proxy com TLS, deixe "auto" ou "true".
    SESSION_COOKIE_SECURE: str = os.getenv("SESSION_COOKIE_SECURE", "auto").strip().lower()
    # IPs/redes dos proxies reversos cujos X-Forwarded-For/Proto merecem
    # confiança. Vazio: nenhum cabeçalho de encaminhamento é aceito.
    TRUSTED_PROXIES: str = os.getenv("TRUSTED_PROXIES", "127.0.0.1,::1")

    # ── Indicadores (RMR) — módulo isolado em /indicadores ──────────────
    # Banco próprio, separado do resto do sistema. Default: SQLite local.
    INDICADORES_DATABASE_URL: str = _env(
        "INDICADORES_DATABASE_URL",
        _sqlite("indicadores"),
    )
    # Conta de serviço do ServiceNow (API REST) — usada só para LEITURA.
    # A senha nunca fica no repositório; vem do ambiente / systemd-creds.
    SN_API_BASE: str = os.getenv("SN_API_BASE", "https://renner.service-now.com")
    SN_API_USER: str = os.getenv("SN_API_USER", "")
    SN_API_PASS: str = _env("SN_API_PASS", "")
    # Proxy de saída (com a senha do @ escapada como %40). Reaproveita o
    # https_proxy do ambiente se não houver um específico.
    # Usa, por padrão, o MESMO proxy que o portal já usa para o ServiceNow
    # (SN_PROXY) — que é o que funciona neste servidor. Cai para https_proxy
    # do ambiente se nada específico for definido.
    SN_API_PROXY: str = (
        os.getenv("SN_API_PROXY", "")
        or os.getenv("SN_PROXY", "")
        or os.getenv("https_proxy", "")
        or os.getenv("HTTPS_PROXY", "")
    )
    # Fila / grupo de atribuição dos indicadores.
    SN_INDIC_QUEUE: str = os.getenv("SN_INDIC_QUEUE", "TI_N2_FLD_RNR_LOJAS_SPARE")
    # Campo de início do TMA ("Data Bouncing"). Configurável porque o nome
    # interno varia por instância; ajuste se necessário.
    SN_TMA_START_FIELD: str = os.getenv("SN_TMA_START_FIELD", "u_data_bouncing")
    # ── Indicadores: fonte das ANS (task_sla) ───────────────────────────
    # Só contam ANS cujo NOME contém este texto (ex.: "SPARE") — evita puxar
    # SLA de outras filas. Ajuste para o nome exato da ANS de Resolução se
    # precisar restringir mais (ex.: "SPARE Resolução").
    SN_SLA_NAME_LIKE: str = os.getenv("SN_SLA_NAME_LIKE", "SPARE")
    # Estágio da ANS considerado (só concluídas evita falso estouro de ANS
    # ainda em andamento). Vazio = não filtra por estágio.
    SN_SLA_STAGE: str = os.getenv("SN_SLA_STAGE", "completed")
    # Filtro extra opcional na task_sla (ex.: para isolar só Resolução).
    SN_SLA_EXTRA: str = os.getenv("SN_SLA_EXTRA", "")
    # Campo de data usado para alocar a ANS no mês.
    SN_SLA_DATE_FIELD: str = os.getenv("SN_SLA_DATE_FIELD", "task.closed_at")

    # ── Indicadores (incident) — estados e filtros configuráveis ────────
    # Valores NUMÉRICOS do campo state em incident:
    #   1 New · 2 In Progress · 3 On Hold · 6 Resolved · 7 Closed · 8 Canceled
    SN_STATE_ABERTO: str = os.getenv("SN_STATE_ABERTO", "1,2,3")        # New, In Progress, On Hold
    SN_STATE_ATENDIMENTO: str = os.getenv("SN_STATE_ATENDIMENTO", "1,2")  # New, In Progress
    SN_STATE_RESOLVIDO: str = os.getenv("SN_STATE_RESOLVIDO", "6,7")     # Resolved, Closed
    SN_STATE_CANCELADO: str = os.getenv("SN_STATE_CANCELADO", "8")
    # Data usada para alocar "tratado/resolvido" no mês.
    SN_RESOLVED_DATE_FIELD: str = os.getenv("SN_RESOLVED_DATE_FIELD", "closed_at")
    # Data usada para alocar o BACKLOG no mês ("data bouncing"); cai para
    # opened_at se o campo não existir na instância.
    SN_BACKLOG_DATE_FIELD: str = os.getenv("SN_BACKLOG_DATE_FIELD", "u_data_bouncing")
    # Campo agrupador de "Abertos por status" (padrão: state; troque por um
    # campo custom de estágio, ex.: u_status_spare, se houver).
    SN_STATUS_FIELD: str = os.getenv("SN_STATUS_FIELD", "state")
    # Campo de BU/empresa (Renner, Youcom, Camicado, Ashua).
    SN_BU_FIELD: str = os.getenv("SN_BU_FIELD", "company")
    # Fragmento de query (encoded) para "Priorizados" — campo custom
    # "It will be prioritized? = Yes". Sem um valor válido, o KPI fica oculto
    # para não exibir número errado.
    SN_PRIORITIZED_QUERY: str = os.getenv("SN_PRIORITIZED_QUERY", "u_prioritized=true")
    # Subcategorias (usadas com LIKE, robusto a variações de valor/rotulo).
    SN_SUB_SLED_LIKE: str = os.getenv("SN_SUB_SLED_LIKE", "sled")
    SN_SUB_COLETOR_LIKE: str = os.getenv("SN_SUB_COLETOR_LIKE", "coletor")
    # Recalcula os indicadores em segundo plano a cada N minutos (0 desliga).
    # A tela relê o snapshot a cada 2 min, então 2 mantém o painel sempre atual.
    SN_INDIC_REFRESH_MIN: int = int(os.getenv("SN_INDIC_REFRESH_MIN", "2") or 2)

    # ── Automações (encerramento/encaminhamento) — módulo isolado ───────
    # Banco próprio, separado do portal. Default: SQLite local.
    AUTOMACOES_DATABASE_URL: str = _env(
        "AUTOMACOES_DATABASE_URL",
        _sqlite("automacoes"),
    )

    # ── Monitoramento (saúde e falhas) — módulo isolado ─────────────────
    MONITORAMENTO_DATABASE_URL: str = _env(
        "MONITORAMENTO_DATABASE_URL",
        _sqlite("monitoramento"),
    )

    # ── Alertas por e-mail (SMTP) ───────────────────────────────────────
    # Apenas VALORES PADRÃO: a configuração efetiva fica no banco de
    # monitoramento e é editável em Parâmetros → Monitoramento. A senha só
    # vem do cofre (SMTP_SENHA) ou do store cifrado — nunca do código.
    SMTP_HOST: str = os.getenv("SMTP_HOST", "")
    SMTP_PORT: int = int(os.getenv("SMTP_PORT", "25"))
    SMTP_SEGURANCA: str = os.getenv("SMTP_SEGURANCA", "none")  # none|starttls|ssl
    SMTP_USUARIO: str = os.getenv("SMTP_USUARIO", "")
    SMTP_REMETENTE: str = os.getenv("SMTP_REMETENTE", "portal-spare@lojasrenner.com.br")
    ALERTA_EMAIL_TO: str = os.getenv("ALERTA_EMAIL_TO", "")


    # ── Orçamento de Manutenção (reparo de coletores e SLEDs) — módulo isolado
    # Banco PRÓPRIO; contrato em docs/ORCAMENTO_MANUTENCAO.md.
    ORCAMENTO_MANUTENCAO_DATABASE_URL: str = _env(
        "ORCAMENTO_MANUTENCAO_DATABASE_URL",
        _sqlite("orcamento_manutencao"),
    )

    # ── Obsolescência do parque de coletores (/obsolescencia) ───────────
    # Banco PRÓPRIO; contrato em docs/MDM_OBSOLESCENCIA.md.
    OBSOLESCENCIA_DATABASE_URL: str = _env(
        "OBSOLESCENCIA_DATABASE_URL",
        _sqlite("obsolescencia"),
    )
    # Console do MDM de Coletores (Workspace ONE / AirWatch).
    MDM_BASE_URL: str = os.getenv("MDM_BASE_URL", "https://cn258.awmdm.com")
    # Certificado ao falar com o MDM: por padrão segue VERIFY_SSL (o proxy
    # corporativo intercepta o TLS). Um caminho de CA aqui liga a
    # verificação com essa cadeia.
    MDM_CA_BUNDLE: str = os.getenv("MDM_CA_BUNDLE", "")

    # ── Trilha do Ativo (núcleo de rastreabilidade e relógios) ──────────
    # Banco próprio: é a espinha dos processos e não divide arquivo com
    # nenhum módulo de tela.
    TRILHA_DATABASE_URL: str = _env(
        "TRILHA_DATABASE_URL",
        _sqlite("trilha"),
    )

    # ── Separação e Expedição (A15) ─────────────────────────────────────
    SEPARACAO_DATABASE_URL: str = _env(
        "SEPARACAO_DATABASE_URL",
        _sqlite("separacao"),
    )

    # ── Projetos de loja (A16) ──────────────────────────────────────────
    PROJETOS_DATABASE_URL: str = _env(
        "PROJETOS_DATABASE_URL",
        _sqlite("projetos"),
    )

    # ── Logística reversa (A17) ─────────────────────────────────────────
    REVERSA_DATABASE_URL: str = _env(
        "REVERSA_DATABASE_URL",
        _sqlite("reversa"),
    )

    # ── Inventário e contagem (A18) ─────────────────────────────────────
    INVENTARIO_DATABASE_URL: str = _env(
        "INVENTARIO_DATABASE_URL",
        _sqlite("inventario"),
    )

    # ── Venda de ativos (A11) ───────────────────────────────────────────
    VENDA_DATABASE_URL: str = _env(
        "VENDA_DATABASE_URL",
        _sqlite("venda"),
    )

    # ── Regularização de ativo (A19) ────────────────────────────────────
    REGULARIZACAO_DATABASE_URL: str = _env(
        "REGULARIZACAO_DATABASE_URL",
        _sqlite("regularizacao"),
    )

    # ── Planejamento de compras (Orçamento Spare) ───────────────────────
    PLANEJAMENTO_DATABASE_URL: str = _env(
        "PLANEJAMENTO_DATABASE_URL",
        _sqlite("planejamento_spare"),
    )

    # ── Consulta Times (acesso específico) ──────────────────────────────
    CONSULTA_TIMES_DATABASE_URL: str = _env(
        "CONSULTA_TIMES_DATABASE_URL",
        _sqlite("consulta_times"),
    )

    # ── Atendimento a chamados (A20) ────────────────────────────────────
    ATENDIMENTO_DATABASE_URL: str = _env(
        "ATENDIMENTO_DATABASE_URL",
        _sqlite("atendimento"),
    )

    # ── Bancadas de triagem e reparo (A02, A03, A04) ────────────────────
    BANCADA_DATABASE_URL: str = _env(
        "BANCADA_DATABASE_URL",
        _sqlite("bancada"),
    )

    # ── Preparação: configuração, montagem e internalização ─────────────
    PREPARACAO_DATABASE_URL: str = _env(
        "PREPARACAO_DATABASE_URL",
        _sqlite("preparacao"),
    )

    # ── Destinação (A09 a A13) ──────────────────────────────────────────
    DESTINACAO_DATABASE_URL: str = _env(
        "DESTINACAO_DATABASE_URL",
        _sqlite("destinacao"),
    )

    # ── Assistência externa e devolução a terceiros (A05, A14) ──────────
    EXTERNO_DATABASE_URL: str = _env(
        "EXTERNO_DATABASE_URL",
        _sqlite("externo"),
    )

    # ── Controle de Orçamento — Execução CAPEX (/controle-orcamento) ────
    # Banco próprio, separado do portal. Default: SQLite local.
    ORCAMENTO_EXEC_DATABASE_URL: str = _env(
        "ORCAMENTO_EXEC_DATABASE_URL",
        _sqlite("controle_orcamento_exec"),
    )
    # ── Orçamento Spare (CAPEX + OPEX da área) — mesma tela do Infra CSC,
    #    banco e permissão próprios ─────────────────────────────────────
    ORCAMENTO_SPARE_EXEC_DATABASE_URL: str = _env(
        "ORCAMENTO_SPARE_EXEC_DATABASE_URL",
        _sqlite("orcamento_spare_exec"),
    )
    # API de CAPEX do EBS (preenche os valores dos projetos).
    EBS_CAPEX_URL: str = os.getenv(
        "EBS_CAPEX_URL", "https://suporte.lojasrenner.com.br/ebs/api/capex/"
    )
    EBS_CAPEX_PROXY: str = os.getenv("EBS_CAPEX_PROXY", "")
    EBS_CAPEX_TIMEOUT: int = int(os.getenv("EBS_CAPEX_TIMEOUT", "30"))
    # Em branco segue VERIFY_SSL; "true"/"false" fixam só para esta API.
    EBS_CAPEX_VERIFY: str = os.getenv("EBS_CAPEX_VERIFY", "")
    # Autenticação da API de CAPEX (a API exige credencial em chamadas de servidor).
    # Opção A — Basic auth (usuário/senha):
    EBS_CAPEX_USER: str = os.getenv("EBS_CAPEX_USER", "")
    EBS_CAPEX_PASS: str = _env("EBS_CAPEX_PASS", "")
    # Opção B — token/header (ex.: Bearer). Se EBS_CAPEX_TOKEN estiver definido,
    # é enviado como  "<EBS_CAPEX_TOKEN_SCHEME> <token>"  no header indicado.
    EBS_CAPEX_TOKEN: str = _env("EBS_CAPEX_TOKEN", "")
    EBS_CAPEX_TOKEN_SCHEME: str = os.getenv("EBS_CAPEX_TOKEN_SCHEME", "Bearer")
    EBS_CAPEX_AUTH_HEADER: str = os.getenv("EBS_CAPEX_AUTH_HEADER", "Authorization")
    # Conversão de moeda para projetos de Argentina (ARS) e Uruguai (UYU) → BRL.
    # Cotação em REAIS por 1 peso. Se 0, o sistema tenta buscar cotação ao vivo
    # (EBS_CAPEX_FX_URL); se também falhar, não converte e avisa.
    # Cotações fixadas em produção: a busca ao vivo (EBS_CAPEX_FX_URL) não é
    # confiável no ambiente, e sem valor o portal deixa de converter. Rever
    # periodicamente — cotação em código envelhece em silêncio; o ideal é
    # sobrescrever por EBS_CAPEX_ARS_BRL / EBS_CAPEX_UYU_BRL no environment.
    EBS_CAPEX_ARS_BRL: float = float(os.getenv("EBS_CAPEX_ARS_BRL", "0.0034") or 0)
    EBS_CAPEX_UYU_BRL: float = float(os.getenv("EBS_CAPEX_UYU_BRL", "0.13") or 0)
    EBS_CAPEX_FX_URL: str = os.getenv(
        "EBS_CAPEX_FX_URL", "https://economia.awesomeapi.com.br/last/ARS-BRL,UYU-BRL"
    )
    EBS_CAPEX_FX_PROXY: str = os.getenv("EBS_CAPEX_FX_PROXY", "")

    # Módulos com permissão por usuário. "orcamento" não aparece na sidebar
    # do portal — é a tela /controle-orcamento, liberada individualmente.
    # ── EBS Forms (RPA sobre o cliente Oracle Forms) — aditivo ──────────
    # Usuário/senha do robô vêm do cofre local (EBS_FORMS_USER / EBS_FORMS_PASS),
    # resolvidos dentro de integracoes/ebs_forms.py.
    EBS_FORMS_DATABASE_URL: str = _env("EBS_FORMS_DATABASE_URL") or _sqlite("ebs_forms")
    EBS_FORMS_HOME_URL: str = os.getenv(
        "EBS_FORMS_HOME_URL",
        "http://ebscorporativo.lojasrenner.com.br/OA_HTML/OA.jsp?OAFunc=OAHOMEPAGE",
    )
    EBS_FORMS_FUNCAO_URL: str = os.getenv("EBS_FORMS_FUNCAO_URL", "")
    EBS_FORMS_FUNCAO: str = os.getenv("EBS_FORMS_FUNCAO", "Informações Financeiras")
    EBS_FORMS_RESPONSABILIDADE: str = os.getenv("EBS_FORMS_RESPONSABILIDADE", "RENNER_FA_CONSULTA")
    EBS_FORMS_LIVROS: str = os.getenv("EBS_FORMS_LIVROS", "FA_RENNER,FA_RENNER_FIS")
    EBS_FORMS_PROXY: str = os.getenv("EBS_FORMS_PROXY", "")
    EBS_FORMS_VERIFY: str = os.getenv("EBS_FORMS_VERIFY", "")  # em branco segue VERIFY_SSL
    EBS_FORMS_TIMEOUT: str = os.getenv("EBS_FORMS_TIMEOUT", "40")
    EBS_FORMS_DISPLAY: str = os.getenv("EBS_FORMS_DISPLAY", "")  # vazio: :99 (produção) / :98 (testes)
    EBS_FORMS_TELA: str = os.getenv("EBS_FORMS_TELA", "1280x900x24")
    EBS_FORMS_JAVA: str = os.getenv("EBS_FORMS_JAVA", "")
    EBS_FORMS_JAVAC: str = os.getenv("EBS_FORMS_JAVAC", "")
    EBS_FORMS_JAVA_OPCOES: str = os.getenv("EBS_FORMS_JAVA_OPCOES", "")
    EBS_FORMS_PARAMS: str = os.getenv("EBS_FORMS_PARAMS", "")  # sobrepõe parâmetros do jnlp: a=1;b=2
    EBS_FORMS_JARS_EXTRA: str = os.getenv('EBS_FORMS_JARS_EXTRA', 'fndi18n.jar')  # jars adicionais, separados por vírgula
    EBS_FORMS_VNC: str = os.getenv('EBS_FORMS_VNC', 'nao')             # sim = acompanhar a tela por VNC (localhost)
    EBS_FORMS_VNC_PORTA: str = os.getenv('EBS_FORMS_VNC_PORTA', '5900')
    EBS_FORMS_CAPTURA: str = os.getenv('EBS_FORMS_CAPTURA', 'java')  # java = desenha as janelas; robot = captura do X
    EBS_FORMS_DETALHE: str = os.getenv('EBS_FORMS_DETALHE', 'nao')  # sim = devolve também a tela inteira
    # Sessão viva: reabrir o Forms custa 20-40 s; mantê-lo aberto faz a
    # consulta seguinte custar segundos.
    EBS_FORMS_SESSAO_VIVA: str = os.getenv('EBS_FORMS_SESSAO_VIVA', 'sim')
    EBS_FORMS_SESSAO_OCIOSA_MIN: str = os.getenv('EBS_FORMS_SESSAO_OCIOSA_MIN', '10')
    EBS_FORMS_SESSAO_MAXIMA_MIN: str = os.getenv('EBS_FORMS_SESSAO_MAXIMA_MIN', '60')
    # Nomes dos componentes da tela Localizar Ativos (do mapa em data/ebs_forms/depuracao)
    EBS_FORMS_CAMPO_CRITERIO: str = os.getenv('EBS_FORMS_CAMPO_CRITERIO', 'VTextField200')
    EBS_FORMS_CAMPO_LIVRO: str = os.getenv('EBS_FORMS_CAMPO_LIVRO', 'VTextField209')
    EBS_FORMS_BOTAO_LOCALIZAR: str = os.getenv('EBS_FORMS_BOTAO_LOCALIZAR', 'Button18')
    EBS_FORMS_BOTAO_LIMPAR: str = os.getenv('EBS_FORMS_BOTAO_LIMPAR', 'Button17')
    EBS_FORMS_BOTAO_LINHAS_ORIGEM: str = os.getenv('EBS_FORMS_BOTAO_LINHAS_ORIGEM', 'Button14')
    # o X do Forms é desenhado: fechar janela é pelo menu do sistema, por um
    # caminho da barra de menus ou por tecla — varia conforme a instalação
    EBS_FORMS_FECHAR_MENUS: str = os.getenv('EBS_FORMS_FECHAR_MENUS', 'Arquivo|Fechar Janela;Arquivo|Fechar;Janela|Fechar')
    EBS_FORMS_FECHAR_TECLA: str = os.getenv('EBS_FORMS_FECHAR_TECLA', 'CTRL+F4')
    EBS_FORMS_CLASSE: str = os.getenv("EBS_FORMS_CLASSE", "")
    EBS_FORMS_ESPERA_JVM: str = os.getenv("EBS_FORMS_ESPERA_JVM", "180")

    # Módulos com permissão por usuário e as AÇÕES que existem em cada um.
    # A tela de permissões só oferece estas; `permissions_set` descarta o
    # resto (não há o que "exportar" na tela de boas-vindas). Módulo fora
    # desta tabela funciona para admin e nega 403 para todo o resto.
    # Os níveis (view < create/edit < admin) valem só dentro do módulo:
    # `admin` aqui nunca é administrador do portal (`is_admin`).
    MODULE_ACTIONS: dict[str, tuple[str, ...]] = {
        "bemvindo": ("view",),
        "consulta": ("view", "export"),
        "recebimento": ("view", "create", "edit", "export", "admin"),
        "reparos": ("view", "edit", "admin"),
        "status": ("view",),
        "parametros": ("view", "admin"),
        "identificacao": ("view", "create", "admin"),
        "servicenow": ("view", "create", "edit"),
        "rastreio": ("view", "edit"),
        "orcamento": ("view", "edit", "admin"),            # /controle-orcamento
        "orcamento_spare": ("view", "edit", "admin"),
        "ebs_forms": ("view", "create", "admin"),
        "automacoes": ("view", "admin"),
        "orcamento_manutencao": ("view", "create", "edit", "export", "admin"),
        "trilha": ("view", "admin"),
        "torre": ("view", "admin"),
        "atendimento": ("view", "edit", "admin"),
        "preparacao": ("view", "edit", "admin"),
        "separacao": ("view", "create", "edit", "admin"),
        "projetos": ("view", "create", "edit", "admin"),
        "reversa": ("view", "create", "edit", "admin"),
        "inventario": ("view", "create", "edit", "admin"),
        "regularizacao": ("view", "create", "edit", "admin"),
        "externo": ("view", "edit", "admin"),
        "destinacao": ("view", "edit", "admin"),
        "obsolescencia": ("view",),   # coleta e credencial são do admin do portal
        "consulta_times": ("view", "edit", "admin"),
        "venda": ("view", "edit", "admin"),
    }
    MODULES: list[str] = list(MODULE_ACTIONS)
    CLOSED_STATUSES: set[str] = {
        "VENDA", "ENVIADO LOJA", "INTERNALIZADO", "S/ REPARO", "DESCARTE"
    }
    RESULT_STATUS_MAP: dict[str, str] = {
        "DESCARTE": "S/ REPARO",
        "DIRETO LOJA": "ENVIADO LOJA",
        "EM TRIAGEM": "EM TRIAGEM",
        "INTERNALIZAR": "INTERNALIZADO",
        "TRATATIVA DE SALDO": "TRATATIVA DE SALDO",
    }

@lru_cache
def get_settings() -> Settings:
    s = Settings()
    s.UPLOAD.mkdir(parents=True, exist_ok=True)
    s.DB_DIR.mkdir(parents=True, exist_ok=True)
    return s
