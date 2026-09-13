"""Núcleo do portal: o que não é rota, banco nem integração externa.

    security.py     sessão, permissões, rate limit e middlewares de segurança
    notificador.py  canal de alertas por e-mail

A configuração (`config.py`) fica na raiz de propósito: é lida também pelos
aplicativos isolados em `apps/` e pelos utilitários em `scripts/`.
"""
