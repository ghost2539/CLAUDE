from __future__ import annotations
import os
from pathlib import Path
from functools import lru_cache

_ROOT = Path(__file__).parent


def _env(nome: str, default: str = "") -> str:
    """Variável de ambiente com `@cofre:CHAVE@` já resolvido.

    Deixa o `environment` legível e sem senha: a linha fica inteira lá, e só
    o pedaço secreto vem do cofre. Ex.:

        DATABASE_URL=postgresql+psycopg2://portal:@cofre:DB_SENHA@@host/base

    O marcador usa `@` porque o arquivo é carregado com `. arquivo` pelos
    scripts; `${...}` seria comido pelo bash e a variável chegaria vazia.
    """
    bruto = os.getenv(nome, default)
    # Variável DECLARADA e vazia cai no padrão. No arquivo de ambiente é
    # natural deixar "CHAVE=" como "não configurei isto", mas os.getenv
    # devolveria "" e engoliria o padrão — foi assim que os bancos dos
    # módulos isolados ficaram com URL vazia e não subiram.
    # (Proxy é exceção deliberada e tem tratamento próprio em _proxy().)
    if not bruto:
        return default
    if "@cofre:" not in bruto:
        return bruto
    try:
        from core.cofre import expandir
        return expandir(bruto)
    except Exception:  # noqa: BLE001 — sem cofre, devolve como veio
        return bruto


def _env_obrigatorio(nome: str) -> str:
    v = _env(nome)
    if not v:
        raise RuntimeError(
            f"{nome} não está definido. Configure em "
            f"~/.config/portal-spare/environment (veja .env.example)."
        )
    return v


def _proxy(*nomes: str) -> str:
    """Primeiro proxy definido na ordem dada.

    Uma variável DECLARADA e vazia é uma decisão ("aqui não tem proxy") e
    encerra a busca; só a ausência total continua procurando. Sem isso, um
    `https_proxy` exportado no perfil do servidor entraria no portal sem
    ninguém ter configurado — e toda chamada de API iria para um endereço
    que talvez nem exista no destino.
    """
    for nome in nomes:
        if nome in os.environ:
            return _env(nome, "").strip()
    return ""


def _segredo(nome: str, default: str = "") -> str:
    """Valor que é segredo: cofre corporativo, cofre local, ambiente."""
    try:
        from core.cofre import obter
        return obter(nome, default)
    except Exception:  # noqa: BLE001
        return os.getenv(nome, default)


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

    DATABASE_URL: str = _env_obrigatorio("DATABASE_URL")
    SESSION_SECRET: str = _env_obrigatorio("PORTAL_SESSION_SECRET")
    SESSION_TTL: int = int(_env("SESSION_TTL_MINUTES", "480")) * 60

    EBS_LOGIN_URL: str = _env("EBS_LOGIN_URL", "")
    EBS_SEARCH_URL: str = _env("EBS_SEARCH_URL", "")
    VERIFY_SSL: bool = _env("VERIFY_SSL", "false").lower() == "true"
    TIMEOUT: int = int(_env("TIMEOUT_SECONDS", "15"))
    MAX_WORKERS: int = int(_env("MAX_WORKERS", "40"))
    CREDENTIALS_DIRECTORY: str = _env("CREDENTIALS_DIRECTORY", "")

    HOST: str = _env("HOST", "0.0.0.0")
    PORT: int = int(_env("PORT", "8901"))
    WORKERS: int = int(_env("WORKERS", "1"))

    DEFAULT_HOURLY_RATE: float = float(_env("DEFAULT_VALOR_HORA", "150"))
    INITIAL_ADMIN_LOGIN: str = _env("INITIAL_ADMIN_LOGIN", "")
    INITIAL_ADMIN_PASSWORD: str = _env("INITIAL_ADMIN_PASSWORD", "")
    UPLOAD_MAX_MB: int = int(_env("UPLOAD_MAX_MB", "50"))

    RATE_LIMIT_LOGIN: str = _env("RATE_LIMIT_LOGIN", "5/minute")
    RATE_LIMIT_API: str = _env("RATE_LIMIT_API", "120/minute")

    SSL_CERTFILE: str = _env("SSL_CERTFILE", "")
    SSL_KEYFILE: str = _env("SSL_KEYFILE", "")

    # ── Indicadores (RMR) — módulo isolado em /indicadores ──────────────
    # Banco próprio, separado do resto do sistema. Default: SQLite local.
    INDICADORES_DATABASE_URL: str = _env(
        "INDICADORES_DATABASE_URL",
        _sqlite("indicadores"),
    )
    # Conta de serviço do ServiceNow (API REST) — usada só para LEITURA.
    # A senha nunca fica no repositório; vem do ambiente / systemd-creds.
    SN_API_BASE: str = _env("SN_API_BASE", "https://renner.service-now.com")
    SN_API_USER: str = _env("SN_API_USER", "")
    SN_API_PASS: str = _env("SN_API_PASS", "")
    # Proxy de saída para o ServiceNow (senha do @ escapada como %40).
    #
    # Declarar SN_PROXY= ou SN_API_PROXY= VAZIO no environment significa
    # "sem proxy" e encerra a busca. Só quando a variável não existe é que
    # o https_proxy do sistema é aproveitado — assim um proxy no perfil do
    # servidor não entra no portal sem alguém ter pedido.
    SN_API_PROXY: str = _proxy("SN_API_PROXY", "SN_PROXY", "https_proxy", "HTTPS_PROXY")
    # Fila / grupo de atribuição dos indicadores.
    SN_INDIC_QUEUE: str = _env("SN_INDIC_QUEUE", "TI_N2_FLD_RNR_LOJAS_SPARE")
    # Campo de início do TMA ("Data Bouncing"). Configurável porque o nome
    # interno varia por instância; ajuste se necessário.
    SN_TMA_START_FIELD: str = _env("SN_TMA_START_FIELD", "u_data_bouncing")
    # ── Indicadores: fonte das ANS (task_sla) ───────────────────────────
    # Só contam ANS cujo NOME contém este texto (ex.: "SPARE") — evita puxar
    # SLA de outras filas. Ajuste para o nome exato da ANS de Resolução se
    # precisar restringir mais (ex.: "SPARE Resolução").
    SN_SLA_NAME_LIKE: str = _env("SN_SLA_NAME_LIKE", "SPARE")
    # Estágio da ANS considerado (só concluídas evita falso estouro de ANS
    # ainda em andamento). Vazio = não filtra por estágio.
    SN_SLA_STAGE: str = _env("SN_SLA_STAGE", "completed")
    # Filtro extra opcional na task_sla (ex.: para isolar só Resolução).
    SN_SLA_EXTRA: str = _env("SN_SLA_EXTRA", "")
    # Campo de data usado para alocar a ANS no mês.
    SN_SLA_DATE_FIELD: str = _env("SN_SLA_DATE_FIELD", "task.closed_at")

    # ── Indicadores (incident) — estados e filtros configuráveis ────────
    # Valores NUMÉRICOS do campo state em incident:
    #   1 New · 2 In Progress · 3 On Hold · 6 Resolved · 7 Closed · 8 Canceled
    SN_STATE_ABERTO: str = _env("SN_STATE_ABERTO", "1,2,3")        # New, In Progress, On Hold
    SN_STATE_ATENDIMENTO: str = _env("SN_STATE_ATENDIMENTO", "1,2")  # New, In Progress
    SN_STATE_RESOLVIDO: str = _env("SN_STATE_RESOLVIDO", "6,7")     # Resolved, Closed
    SN_STATE_CANCELADO: str = _env("SN_STATE_CANCELADO", "8")
    # Data usada para alocar "tratado/resolvido" no mês.
    SN_RESOLVED_DATE_FIELD: str = _env("SN_RESOLVED_DATE_FIELD", "closed_at")
    # Data usada para alocar o BACKLOG no mês ("data bouncing"); cai para
    # opened_at se o campo não existir na instância.
    SN_BACKLOG_DATE_FIELD: str = _env("SN_BACKLOG_DATE_FIELD", "u_data_bouncing")
    # Campo agrupador de "Abertos por status" (padrão: state; troque por um
    # campo custom de estágio, ex.: u_status_spare, se houver).
    SN_STATUS_FIELD: str = _env("SN_STATUS_FIELD", "state")
    # Campo de BU/empresa (Renner, Youcom, Camicado, Ashua).
    SN_BU_FIELD: str = _env("SN_BU_FIELD", "company")
    # Fragmento de query (encoded) para "Priorizados" — campo custom
    # "It will be prioritized? = Yes". Sem um valor válido, o KPI fica oculto
    # para não exibir número errado.
    SN_PRIORITIZED_QUERY: str = _env("SN_PRIORITIZED_QUERY", "u_prioritized=true")
    # Subcategorias (usadas com LIKE, robusto a variações de valor/rotulo).
    SN_SUB_SLED_LIKE: str = _env("SN_SUB_SLED_LIKE", "sled")
    SN_SUB_COLETOR_LIKE: str = _env("SN_SUB_COLETOR_LIKE", "coletor")
    # Recalcula os indicadores em segundo plano a cada N minutos (0 desliga).
    # A tela relê o snapshot a cada 2 min, então 2 mantém o painel sempre atual.
    SN_INDIC_REFRESH_MIN: int = int(_env("SN_INDIC_REFRESH_MIN", "2") or 2)

    # ── Automações (encerramento/encaminhamento) — módulo isolado ───────
    # Banco próprio, separado do portal. Default: SQLite local.
    AUTOMACOES_DATABASE_URL: str = _env(
        "AUTOMACOES_DATABASE_URL",
        _sqlite("automacoes"),
    )
    # Horários (hora local) em que a rotina roda sozinha. CSV de horas.
    AUTOMACOES_HORARIOS: str = _env("AUTOMACOES_HORARIOS", "7,12,16")

    # ── Monitoramento (saúde e falhas) — módulo isolado ─────────────────
    MONITORAMENTO_DATABASE_URL: str = _env(
        "MONITORAMENTO_DATABASE_URL",
        _sqlite("monitoramento"),
    )

    # ── Alertas por e-mail (SMTP) ───────────────────────────────────────
    # Apenas VALORES PADRÃO: a configuração efetiva fica no banco de
    # monitoramento e é editável em Parâmetros → Monitoramento. A senha só
    # vem do cofre (SMTP_SENHA) ou do store cifrado — nunca do código.
    SMTP_HOST: str = _env("SMTP_HOST", "")
    SMTP_PORT: int = int(_env("SMTP_PORT", "25"))
    SMTP_SEGURANCA: str = _env("SMTP_SEGURANCA", "none")  # none|starttls|ssl
    SMTP_USUARIO: str = _env("SMTP_USUARIO", "")
    SMTP_REMETENTE: str = _env("SMTP_REMETENTE", "portal-spare@lojasrenner.com.br")
    ALERTA_EMAIL_TO: str = _env("ALERTA_EMAIL_TO", "raphael.steilein@lojasrenner.com.br")

    # ── Orçamento do SPARE (CAPEX da área) — módulo isolado ─────────────
    # Banco PRÓPRIO, separado do /controle-orcamento e do portal.
    ORCAMENTO_SPARE_DATABASE_URL: str = _env(
        "ORCAMENTO_SPARE_DATABASE_URL",
        _sqlite("orcamento_spare"),
    )

    # ── Controle de Orçamento — Execução CAPEX (/controle-orcamento) ────
    # Banco próprio, separado do portal. Default: SQLite local.
    ORCAMENTO_EXEC_DATABASE_URL: str = _env(
        "ORCAMENTO_EXEC_DATABASE_URL",
        _sqlite("controle_orcamento_exec"),
    )
    # API de CAPEX do EBS (preenche os valores dos projetos).
    EBS_CAPEX_URL: str = _env(
        "EBS_CAPEX_URL", "https://suporte.lojasrenner.com.br/ebs/api/capex/"
    )
    EBS_CAPEX_PROXY: str = _env("EBS_CAPEX_PROXY", "")
    EBS_CAPEX_TIMEOUT: int = int(_env("EBS_CAPEX_TIMEOUT", "30"))
    EBS_CAPEX_VERIFY: bool = _env("EBS_CAPEX_VERIFY", "false").lower() == "true"
    # Autenticação da API de CAPEX (a API exige credencial em chamadas de servidor).
    # Opção A — Basic auth (usuário/senha):
    EBS_CAPEX_USER: str = _env("EBS_CAPEX_USER", "")
    EBS_CAPEX_PASS: str = _env("EBS_CAPEX_PASS", "")
    # Opção B — token/header (ex.: Bearer). Se EBS_CAPEX_TOKEN estiver definido,
    # é enviado como  "<EBS_CAPEX_TOKEN_SCHEME> <token>"  no header indicado.
    EBS_CAPEX_TOKEN: str = _env("EBS_CAPEX_TOKEN", "")
    EBS_CAPEX_TOKEN_SCHEME: str = _env("EBS_CAPEX_TOKEN_SCHEME", "Bearer")
    EBS_CAPEX_AUTH_HEADER: str = _env("EBS_CAPEX_AUTH_HEADER", "Authorization")
    # Conversão de moeda para projetos de Argentina (ARS) e Uruguai (UYU) → BRL.
    # Cotação em REAIS por 1 peso. Se 0, o sistema tenta buscar cotação ao vivo
    # (EBS_CAPEX_FX_URL); se também falhar, não converte e avisa.
    EBS_CAPEX_ARS_BRL: float = float(_env("EBS_CAPEX_ARS_BRL", "0") or 0)
    EBS_CAPEX_UYU_BRL: float = float(_env("EBS_CAPEX_UYU_BRL", "0") or 0)
    EBS_CAPEX_FX_URL: str = _env(
        "EBS_CAPEX_FX_URL", "https://economia.awesomeapi.com.br/last/ARS-BRL,UYU-BRL"
    )
    EBS_CAPEX_FX_PROXY: str = _env("EBS_CAPEX_FX_PROXY", "")

    # Módulos com permissão por usuário. "orcamento" não aparece na sidebar
    # do portal — é a tela /controle-orcamento, liberada individualmente.
    # ── EBS Forms (RPA sobre o cliente Oracle Forms) ────────────────────
    # Usuário/senha do robô vêm SÓ do cofre (EBS_FORMS_USER / EBS_FORMS_PASS).
    EBS_FORMS_DATABASE_URL: str = _env("EBS_FORMS_DATABASE_URL", _sqlite("ebs_forms"))
    EBS_FORMS_HOME_URL: str = _env(
        "EBS_FORMS_HOME_URL",
        "http://ebscorporativo.lojasrenner.com.br/OA_HTML/OA.jsp?OAFunc=OAHOMEPAGE",
    )
    # Link que a home chama ao clicar na função (RF.jsp?function_id=...). Sem
    # ele o módulo tenta achar o link pelo nome da função na home.
    EBS_FORMS_FUNCAO_URL: str = _env("EBS_FORMS_FUNCAO_URL", "")
    EBS_FORMS_FUNCAO: str = _env("EBS_FORMS_FUNCAO", "Informações Financeiras")
    EBS_FORMS_RESPONSABILIDADE: str = _env("EBS_FORMS_RESPONSABILIDADE", "RENNER_FA_CONSULTA")
    EBS_FORMS_LIVROS: str = _env("EBS_FORMS_LIVROS", "FA_RENNER,FA_RENNER_FIS")
    EBS_FORMS_PROXY: str = _env("EBS_FORMS_PROXY", "")
    EBS_FORMS_VERIFY: str = _env("EBS_FORMS_VERIFY", "false")
    EBS_FORMS_TIMEOUT: str = _env("EBS_FORMS_TIMEOUT", "40")
    EBS_FORMS_DISPLAY: str = _env("EBS_FORMS_DISPLAY", ":99")
    EBS_FORMS_TELA: str = _env("EBS_FORMS_TELA", "1280x900x24")
    EBS_FORMS_JAVA: str = _env("EBS_FORMS_JAVA", "")
    EBS_FORMS_JAVAC: str = _env("EBS_FORMS_JAVAC", "")
    EBS_FORMS_JAVA_OPCOES: str = _env("EBS_FORMS_JAVA_OPCOES", "")
    EBS_FORMS_PARAMS: str = _env("EBS_FORMS_PARAMS", "")  # sobrepõe parâmetros do jnlp: a=1;b=2
    EBS_FORMS_CLASSE: str = _env("EBS_FORMS_CLASSE", "")
    EBS_FORMS_ESPERA_JVM: str = _env("EBS_FORMS_ESPERA_JVM", "180")

    MODULES: list[str] = [
        "bemvindo", "consulta", "recebimento", "reparos", "status", "parametros",
        "identificacao", "servicenow", "rastreio", "orcamento",
        "orcamento_spare", "ebs_forms"
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
