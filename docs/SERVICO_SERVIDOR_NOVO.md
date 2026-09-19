# Pedido à infraestrutura — servidor novo do Portal SPARE

Documento para entregar a quem tem root no servidor. O portal roda como
**serviço do sistema** (`/etc/systemd/system/portal-spare.service`), sob uma
conta de serviço própria. O pedido é curto: fora a instalação da unit e os
caminhos que ela precisa, o resto é feito depois pela equipe do portal,
dentro da pasta da aplicação.

> **Nenhuma senha é enviada à infraestrutura.** As credenciais são gravadas
> cifradas pela equipe do portal, no servidor, depois que a pasta existir.

Toda a aplicação vive em `/var/www/vcreports/portal-spare`. Nada é instalado
ou gravado fora dela, exceto o que estiver marcado abaixo.

---

## 1. Pasta da aplicação

```bash
mkdir -p /var/www/vcreports/portal-spare
chown -R <conta-de-rede> /var/www/vcreports/portal-spare
```

A conta de rede é a de quem opera o portal, informada no chamado. Ela precisa
poder criar subpastas ali (`venv/`, `data/`).

## 2. Python

```bash
dnf install -y python3 python3-pip
```

O ambiente virtual é criado depois pela equipe do portal, dentro da pasta:
`python3 -m venv --system-site-packages venv`. O `--system-site-packages` é o
que permite enxergar o módulo do cofre corporativo, instalado no Python do
sistema.

## 3. Serviço do sistema

A unit versionada é `deploy/portal_spare.service`. Ela roda sob a conta de
serviço `portalspare` (sem shell de login), não sob a conta de quem opera:

```bash
cp deploy/portal_spare.service /etc/systemd/system/portal-spare.service
systemctl daemon-reload && systemctl enable --now portal-spare
```

**Nada é criado fora da pasta da aplicação.** A unit lê tudo de dentro dela:

*   `data/environment` — só configuração, **nenhum segredo** (600). É o
    `EnvironmentFile=` da unit; o modelo está em
    `deploy/environment.servidor-novo`.
*   `data/cofre/` — o cofre local cifrado (700), apontado por
    `PORTAL_COFRE_DIR` na unit. Precisa ser um caminho explícito porque a
    unit usa `ProtectHome=yes`: com o padrão `~/.config/portal-spare`, o
    portal subiria sem enxergar segredo nenhum.

> `/etc/portal_operacoes_spare/` é do **servidor antigo**
> (`/opt/portal-spare-v2`). No servidor novo esse caminho não existe.

O restart no dia a dia (`systemctl restart portal-spare`) pede root ou uma
regra de sudo para a equipe do portal.

## 4. Acesso ao cofre corporativo

A conta de rede precisa conseguir **ler** `/etc/vcreports/.secrets.env` —
por participação no grupo dono do arquivo, ou por um comando de leitura
disponibilizado pelo time que mantém o cofre.

É dali que o portal obtém as credenciais das integrações. Sem esse acesso o
portal sobe normalmente, mas as integrações que dependem do cofre não
funcionam. Para conferir, a própria conta roda:

```bash
cd /var/www/vcreports/portal-spare
python3 scripts/cofre.py acesso
```

A saída diz o dono, o grupo e o modo do arquivo, se aquela conta consegue ler
e, quando não consegue, o que precisa ser pedido.

## 5. Portas de entrada (rede interna)

| Porta | Uso |
|---|---|
| 8901 | Portal — **todas** as telas |

Só uma porta. A tela "Consulta de Ativos — Times", que no sistema antigo
atendia na 8502, agora é um endereço do próprio portal
(`/consulta-times`) — não há segundo serviço nem segunda porta.

A 8901 está acima de 1024, então o serviço a abre sem privilégio.

## 6. Saída de rede — sem proxy

| Destino | Porta |
|---|---|
| `renner.service-now.com` | 443 |
| `loginsso.lojasrenner.com.br` | 443 |
| `api.correios.com.br` | 443 |
| `ebscorporativo.lojasrenner.com.br` | 80 e 443 |
| `suporte.lojasrenner.com.br` | 443 |
| base Oracle do EBS | 1521 |
| relay SMTP interno | 25 ou 587 *(se os alertas por e-mail forem usados)* |

## 7. Publicação em `suporte.lojasrenner.com.br/portal-spare`

Duas informações são necessárias antes da virada:

1. O encaminhamento **preserva** o caminho `/portal-spare` ao chamar o
   backend, ou **remove** o prefixo? A resposta muda o que precisa ser
   ajustado na aplicação: o front usa caminhos absolutos (`/static`, `/api`)
   e, com o prefixo preservado sem ajuste, as telas não carregam.
2. Qual o **IP do proxy**? Ele entra em `--forwarded-allow-ips` na unit; sem
   ele, todo acesso fica registrado com o IP do proxy na trilha de auditoria,
   em vez do IP de quem usou a tela.

O TLS fica no proxy; o portal atende em HTTP na rede interna.

---

## O que a equipe do portal faz depois

1. Clonar o código na pasta e criar o venv com `--system-site-packages`.
2. Escrever `data/environment` (só configuração — o modelo está em
   `deploy/environment.servidor-novo`) em modo 600, e criar `data/cofre/`
   em modo 700.
3. Gravar os segredos: `python3 scripts/cofre.py definir NOME`.
4. Conferir: `python3 scripts/cofre.py conferir` — inclusive que a cifra em
   uso é `fernet`, e não a de contingência.
5. Restaurar os dados do servidor antigo e validar as telas.

## Conferência final

```bash
systemctl is-active portal-spare
curl -s -o /dev/null -w '%{http_code}\n' http://localhost:8901/
curl -s -o /dev/null -w '%{http_code}\n' http://localhost:8901/consulta-times
journalctl -u portal-spare -n 30 --no-pager

# nenhuma senha em texto claro na configuração:
grep -iE 'pass|senha|secret' /var/www/vcreports/portal-spare/data/environment
# só deve aparecer o marcador @cofre:NOME@

ls -ld /var/www/vcreports/portal-spare/data/cofre    # drwx------
```
