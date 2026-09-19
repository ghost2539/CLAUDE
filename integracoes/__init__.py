"""Clientes dos sistemas externos. Nenhum deles conhece rotas ou banco.

    ebs_oracle.py   consultas diretas na base Oracle do EBS (credencial do os.environ)
    ebs_ativos.py   consulta de ativos por série, etiqueta ou imobilizado, sobre o ebs_oracle
    nfe_sefaz.py    NF-e pela chave de acesso (NFeDistribuicaoDFe) com certificado A1
    sn_catalogo.py  Service Catalog do ServiceNow como o usuário logado
    mdm_airwatch.py MDM (Workspace ONE)

Correios e ServiceNow são falados de dentro dos seus próprios routers, que
guardam também as regras de negócio de cada um.
"""
