from __future__ import annotations
import os
from pathlib import Path
from functools import lru_cache

_ROOT = Path(__file__).parent


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

    DATABASE_URL: str = os.environ["DATABASE_URL"]
    SESSION_SECRET: str = os.environ["PORTAL_SESSION_SECRET"]
    SESSION_TTL: int = int(os.getenv("SESSION_TTL_MINUTES", "480")) * 60

    EBS_LOGIN_URL: str = os.getenv("EBS_LOGIN_URL", "")
    EBS_SEARCH_URL: str = os.getenv("EBS_SEARCH_URL", "")
    VERIFY_SSL: bool = os.getenv("VERIFY_SSL", "false").lower() == "true"
    TIMEOUT: int = int(os.getenv("TIMEOUT_SECONDS", "15"))
    MAX_WORKERS: int = int(os.getenv("MAX_WORKERS", "40"))
    CREDENTIALS_DIRECTORY: str = os.getenv("CREDENTIALS_DIRECTORY", "")

    HOST: str = os.getenv("HOST", "0.0.0.0")
    PORT: int = int(os.getenv("PORT", "8901"))
    # A tela Consulta de Ativos — Times atendia em :8502 no aplicativo antigo.
    # O mesmo processo abre esse segundo listener só para ela; 0 desliga.
    CONSULTA_TIMES_PORTA: int = int(os.getenv("CONSULTA_TIMES_PORTA", "8502"))
    CONSULTA_TIMES_HOST: str = os.getenv("CONSULTA_TIMES_HOST", "")
    WORKERS: int = int(os.getenv("WORKERS", "1"))

    DEFAULT_HOURLY_RATE: float = float(os.getenv("DEFAULT_VALOR_HORA", "150"))
    INITIAL_ADMIN_LOGIN: str = os.getenv("INITIAL_ADMIN_LOGIN", "")
    INITIAL_ADMIN_PASSWORD: str = os.getenv("INITIAL_ADMIN_PASSWORD", "")
    UPLOAD_MAX_MB: int = int(os.getenv("UPLOAD_MAX_MB", "50"))

    RATE_LIMIT_LOGIN: str = os.getenv("RATE_LIMIT_LOGIN", "5/minute")
    RATE_LIMIT_API: str = os.getenv("RATE_LIMIT_API", "120/minute")

    SSL_CERTFILE: str = os.getenv("SSL_CERTFILE", "")
    SSL_KEYFILE: str = os.getenv("SSL_KEYFILE", "")

    # ── Indicadores (RMR) — módulo isolado em /indicadores ──────────────
    # Banco próprio, separado do resto do sistema. Default: SQLite local.
    INDICADORES_DATABASE_URL: str = os.getenv(
        "INDICADORES_DATABASE_URL",
        _sqlite("indicadores"),
    )
    # Conta de serviço do ServiceNow (API REST) — usada só para LEITURA.
    # A senha nunca fica no repositório; vem do ambiente / systemd-creds.
    SN_API_BASE: str = os.getenv("SN_API_BASE", "https://renner.service-now.com")
    SN_API_USER: str = os.getenv("SN_API_USER", "")
    SN_API_PASS: str = os.getenv("SN_API_PASS", "")
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
    AUTOMACOES_DATABASE_URL: str = os.getenv(
        "AUTOMACOES_DATABASE_URL",
        _sqlite("automacoes"),
    )
    # Horários (hora local) em que a rotina roda sozinha. CSV de horas.
    AUTOMACOES_HORARIOS: str = os.getenv("AUTOMACOES_HORARIOS", "7,12,16")

    # ── Monitoramento (saúde e falhas) — módulo isolado ─────────────────
    MONITORAMENTO_DATABASE_URL: str = os.getenv(
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
    ALERTA_EMAIL_TO: str = os.getenv("ALERTA_EMAIL_TO", "raphael.steilein@lojasrenner.com.br")

    # ── Orçamento do SPARE (CAPEX da área) — módulo isolado ─────────────
    # Banco PRÓPRIO, separado do /controle-orcamento e do portal.
    ORCAMENTO_SPARE_DATABASE_URL: str = os.getenv(
        "ORCAMENTO_SPARE_DATABASE_URL",
        _sqlite("orcamento_spare"),
    )

    # ── Orçamento de Manutenção (reparo de coletores e SLEDs) — módulo isolado
    # Banco PRÓPRIO; contrato em docs/ORCAMENTO_MANUTENCAO.md.
    ORCAMENTO_MANUTENCAO_DATABASE_URL: str = os.getenv(
        "ORCAMENTO_MANUTENCAO_DATABASE_URL",
        _sqlite("orcamento_manutencao"),
    )

    # ── Obsolescência do parque de coletores (/obsolescencia) ───────────
    # Banco PRÓPRIO; contrato em docs/MDM_OBSOLESCENCIA.md.
    OBSOLESCENCIA_DATABASE_URL: str = os.getenv(
        "OBSOLESCENCIA_DATABASE_URL",
        _sqlite("obsolescencia"),
    )
    # Console do MDM de Coletores (Workspace ONE / AirWatch).
    MDM_BASE_URL: str = os.getenv("MDM_BASE_URL", "https://cn258.awmdm.com")

    # ── Trilha do Ativo (núcleo de rastreabilidade e relógios) ──────────
    # Banco próprio: é a espinha dos processos e não divide arquivo com
    # nenhum módulo de tela.
    TRILHA_DATABASE_URL: str = os.getenv(
        "TRILHA_DATABASE_URL",
        _sqlite("trilha"),
    )

    # ── Controle de Orçamento — Execução CAPEX (/controle-orcamento) ────
    # Banco próprio, separado do portal. Default: SQLite local.
    ORCAMENTO_EXEC_DATABASE_URL: str = os.getenv(
        "ORCAMENTO_EXEC_DATABASE_URL",
        _sqlite("controle_orcamento_exec"),
    )
    # API de CAPEX do EBS (preenche os valores dos projetos).
    EBS_CAPEX_URL: str = os.getenv(
        "EBS_CAPEX_URL", "https://suporte.lojasrenner.com.br/ebs/api/capex/"
    )
    EBS_CAPEX_PROXY: str = os.getenv("EBS_CAPEX_PROXY", "")
    EBS_CAPEX_TIMEOUT: int = int(os.getenv("EBS_CAPEX_TIMEOUT", "30"))
    EBS_CAPEX_VERIFY: bool = os.getenv("EBS_CAPEX_VERIFY", "false").lower() == "true"
    # Autenticação da API de CAPEX (a API exige credencial em chamadas de servidor).
    # Opção A — Basic auth (usuário/senha):
    EBS_CAPEX_USER: str = os.getenv("EBS_CAPEX_USER", "")
    EBS_CAPEX_PASS: str = os.getenv("EBS_CAPEX_PASS", "")
    # Opção B — token/header (ex.: Bearer). Se EBS_CAPEX_TOKEN estiver definido,
    # é enviado como  "<EBS_CAPEX_TOKEN_SCHEME> <token>"  no header indicado.
    EBS_CAPEX_TOKEN: str = os.getenv("EBS_CAPEX_TOKEN", "")
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
    EBS_FORMS_DATABASE_URL: str = os.getenv("EBS_FORMS_DATABASE_URL") or _sqlite("ebs_forms")
    EBS_FORMS_HOME_URL: str = os.getenv(
        "EBS_FORMS_HOME_URL",
        "http://ebscorporativo.lojasrenner.com.br/OA_HTML/OA.jsp?OAFunc=OAHOMEPAGE",
    )
    EBS_FORMS_FUNCAO_URL: str = os.getenv("EBS_FORMS_FUNCAO_URL", "")
    EBS_FORMS_FUNCAO: str = os.getenv("EBS_FORMS_FUNCAO", "Informações Financeiras")
    EBS_FORMS_RESPONSABILIDADE: str = os.getenv("EBS_FORMS_RESPONSABILIDADE", "RENNER_FA_CONSULTA")
    EBS_FORMS_LIVROS: str = os.getenv("EBS_FORMS_LIVROS", "FA_RENNER,FA_RENNER_FIS")
    EBS_FORMS_PROXY: str = os.getenv("EBS_FORMS_PROXY", "")
    EBS_FORMS_VERIFY: str = os.getenv("EBS_FORMS_VERIFY", "false")
    EBS_FORMS_TIMEOUT: str = os.getenv("EBS_FORMS_TIMEOUT", "40")
    EBS_FORMS_DISPLAY: str = os.getenv("EBS_FORMS_DISPLAY", ":99")
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

    MODULES: list[str] = [
        "bemvindo", "consulta", "recebimento", "reparos", "status", "parametros",
        "identificacao", "servicenow", "rastreio", "orcamento",
        "orcamento_spare", "ebs_forms", "automacoes", "orcamento_manutencao"
    ]
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
