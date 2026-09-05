"""Telas de apresentação em TV — cockpit e dashboards operacionais.

PÚBLICAS por decisão de produto: ficam em painel na parede, sem teclado, e
mostram só número agregado. Por isso a regra que vale aqui é a inversa das
demais telas — **nada individualizado pode sair nestes endpoints**:

- sem nome, matrícula ou produtividade de colaborador;
- sem número de chamado, série, imobilizado ou nome de loja isolado;
- sem valor financeiro por projeto;
- só total, média, percentual e contagem por faixa/estado.

Cada tela busca o seu próprio endpoint e desenha o que vier, seguindo o
contrato de blocos abaixo. Assim, definir um indicador novo é escrever a
consulta que devolve o bloco — a página não muda.

    {
      "titulo": str, "subtitulo": str,
      "atualizado_em": ISO, "pendente": bool,
      "blocos": [
        {"tipo": "kpis",   "titulo": str, "itens": [
            {"rotulo": str, "valor": str, "sub": str,
             "estado": "bom|atencao|grave|critico|neutro"}]},
        {"tipo": "barras", "titulo": str, "sufixo": str, "itens": [
            {"rotulo": str, "valor": float, "serie": int}]},
        {"tipo": "tabela", "titulo": str,
         "colunas": [str], "linhas": [[str]]},
        {"tipo": "texto",  "titulo": str, "texto": str}
      ]
    }
"""
from __future__ import annotations

import logging
from datetime import datetime

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from config import get_settings
from core.security import check_rate_limit

_cfg = get_settings()
_log = logging.getLogger("cockpit")
_DIR = _cfg.STATIC / "cockpit"

router = APIRouter(tags=["Cockpit / TV"], include_in_schema=False)


# ── Telas ───────────────────────────────────────────────────────────────
# rota → (arquivo html, título, subtítulo, chave da API)
TELAS: dict[str, tuple[str, str, str]] = {
    "cockpit-spare": (
        "Cockpit SPARE",
        "Performance do time — visão gerencial",
        "Indicadores do time como um todo. Sem dado por colaborador.",
    ),
    "dash-recebimento": (
        "Recebimento",
        "Entrada de equipamentos",
        "Volume recebido, situação dos lotes e fila de tratativa.",
    ),
    "dash-centralreparos": (
        "Central de Reparos",
        "Frente, retaguarda e coletores/SLEDs",
        "Fila, tempo de atendimento e saída por destino.",
    ),
    "dash-estoques": (
        "Estoques",
        "Níveis e movimentação",
        "Posição por estoque, mínimo e máximo, entradas e saídas.",
    ),
}


def _pagina(chave: str) -> HTMLResponse:
    caminho = _DIR / f"{chave}.html"
    if not caminho.exists():   # tela ainda não publicada
        return HTMLResponse("<h1>Tela não encontrada.</h1>", status_code=404)
    return HTMLResponse(caminho.read_text(encoding="utf-8"))


@router.get("/cockpit-spare", response_class=HTMLResponse)
def pagina_cockpit_spare():
    return _pagina("cockpit-spare")


@router.get("/dash-recebimento", response_class=HTMLResponse)
def pagina_dash_recebimento():
    return _pagina("dash-recebimento")


@router.get("/dash-centralreparos", response_class=HTMLResponse)
def pagina_dash_centralreparos():
    return _pagina("dash-centralreparos")


@router.get("/dash-estoques", response_class=HTMLResponse)
def pagina_dash_estoques():
    return _pagina("dash-estoques")


# ── Dados ───────────────────────────────────────────────────────────────
def _base(chave: str, blocos: list | None = None) -> dict:
    titulo, subtitulo, pendencia = TELAS[chave]
    blocos = blocos or []
    return {
        "titulo": titulo,
        "subtitulo": subtitulo,
        "atualizado_em": datetime.now().isoformat(timespec="seconds"),
        "pendente": not blocos,
        "pendencia": pendencia,
        "blocos": blocos,
    }


@router.get("/api/cockpit/cockpit-spare")
def dados_cockpit_spare(req: Request):
    """Indicadores gerenciais do time. Aguardando definição dos indicadores."""
    check_rate_limit(req, "api")
    return _base("cockpit-spare")


@router.get("/api/cockpit/dash-recebimento")
def dados_dash_recebimento(req: Request):
    """Painel do Recebimento. Aguardando definição dos indicadores."""
    check_rate_limit(req, "api")
    return _base("dash-recebimento")


@router.get("/api/cockpit/dash-centralreparos")
def dados_dash_centralreparos(req: Request):
    """Painel da Central de Reparos. Aguardando definição dos indicadores."""
    check_rate_limit(req, "api")
    return _base("dash-centralreparos")


@router.get("/api/cockpit/dash-estoques")
def dados_dash_estoques(req: Request):
    """Painel de Estoques. Aguardando definição dos indicadores."""
    check_rate_limit(req, "api")
    return _base("dash-estoques")
