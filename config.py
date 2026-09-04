from __future__ import annotations
import os
from pathlib import Path
from functools import lru_cache

class Settings:
    ROOT: Path = Path(__file__).parent
    STATIC: Path = ROOT / "static"
    UPLOAD: Path = ROOT / "data" / "uploads"

    DATABASE_URL: str = os.environ["DATABASE_URL"]
    # Banco EXCLUSIVO do módulo Controle de Orçamento (/tv2), separado do portal
    ORCAMENTO_DATABASE_URL: str = os.getenv(
        "CONTROLE_ORCAMENTO_DATABASE_URL",
        "sqlite:///" + str(ROOT / "data" / "controle_orcamento.db"),
    )
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
        f"sqlite:///{(ROOT / 'data' / 'indicadores.db').as_posix()}",
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

    # ── Controle de Orçamento — Execução CAPEX (/controle-orcamento) ────
    # Banco próprio e SEPARADO do /tv2. Default: SQLite local.
    ORCAMENTO_EXEC_DATABASE_URL: str = os.getenv(
        "ORCAMENTO_EXEC_DATABASE_URL",
        f"sqlite:///{(ROOT / 'data' / 'controle_orcamento_exec.db').as_posix()}",
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
    EBS_CAPEX_ARS_BRL: float = float(os.getenv("EBS_CAPEX_ARS_BRL", "0") or 0)
    EBS_CAPEX_UYU_BRL: float = float(os.getenv("EBS_CAPEX_UYU_BRL", "0") or 0)
    EBS_CAPEX_FX_URL: str = os.getenv(
        "EBS_CAPEX_FX_URL", "https://economia.awesomeapi.com.br/last/ARS-BRL,UYU-BRL"
    )
    EBS_CAPEX_FX_PROXY: str = os.getenv("EBS_CAPEX_FX_PROXY", "")

    MODULES: list[str] = [
        "bemvindo", "consulta", "recebimento", "reparos", "status", "parametros",
        "identificacao", "servicenow", "rastreio"
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
    return s
