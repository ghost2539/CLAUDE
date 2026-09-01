from __future__ import annotations
import os
from pathlib import Path
from functools import lru_cache

class Settings:
    ROOT: Path = Path(__file__).parent
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
    WORKERS: int = int(os.getenv("WORKERS", "1"))

    DEFAULT_HOURLY_RATE: float = float(os.getenv("DEFAULT_VALOR_HORA", "150"))
    INITIAL_ADMIN_LOGIN: str = os.getenv("INITIAL_ADMIN_LOGIN", "")
    INITIAL_ADMIN_PASSWORD: str = os.getenv("INITIAL_ADMIN_PASSWORD", "")
    UPLOAD_MAX_MB: int = int(os.getenv("UPLOAD_MAX_MB", "50"))

    RATE_LIMIT_LOGIN: str = os.getenv("RATE_LIMIT_LOGIN", "5/minute")
    RATE_LIMIT_API: str = os.getenv("RATE_LIMIT_API", "120/minute")

    SSL_CERTFILE: str = os.getenv("SSL_CERTFILE", "")
    SSL_KEYFILE: str = os.getenv("SSL_KEYFILE", "")

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
