"""Clientes dos sistemas externos. Nenhum deles conhece rotas ou banco.

    ebs_service.py  API REST do EBS (autenticação e consultas de ativos)
    ebs_oracle.py   consultas diretas na base Oracle do EBS
    ebs_logged.py   raspagem autenticada do EBS (busca por usuário)
    gestao_compras.py  API do módulo Gestão de Compras (PO e projetos do EBS
                    via HTTP — o caminho que não depende de ler o cofre)

Correios e ServiceNow são falados de dentro dos seus próprios routers, que
guardam também as regras de negócio de cada um.
"""
