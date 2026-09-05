"""Aplicativos que rodam como serviço PRÓPRIO, fora do processo do portal.

    controle_orcamento.py  Controle de Orçamento isolado (porta 8902)
    consulta_times/        Consulta de Ativos para os times (porta 8502)

Cada um sobe com seu próprio systemd (ver `deploy/`), tem banco próprio e
não depende do portal estar no ar. Reaproveitam apenas config, núcleo e
camada de dados deste repositório.
"""
