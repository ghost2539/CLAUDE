"""Camada de dados do Portal SPARE.

Um módulo por banco, cada um totalmente isolado dos demais:

    portal.py          Postgres principal (usuários, recebimentos, reparos…)
    indicadores.py     snapshots do painel de indicadores
    automacoes.py      regras, logs e config das automações
    monitoramento.py   eventos de saúde/falha e config de alertas
    orcamento.py       Controle de Orçamento de portfólio (/tv2)
    orcamento_exec.py  Controle de Orçamento — execução CAPEX

Cada módulo expõe `init_db()` e sua própria `SessionLocal`; nenhum importa
o outro. As URLs de conexão vêm sempre de `config.py`.
"""
