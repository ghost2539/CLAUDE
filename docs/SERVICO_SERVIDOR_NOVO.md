# Pedido à infraestrutura — servidor novo do Portal SPARE

Documento para entregar a quem tem root no servidor. O portal roda como
**serviço de usuário** (`systemd --user`), com a conta de rede de quem opera —
não há usuário de serviço. Por isso o pedido é curto: quase tudo é feito
depois, sem root, dentro da pasta da aplicação.

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

## 3. Serviço no boot — `enable-linger`

O serviço é de usuário. Para continuar no ar depois do logout e voltar
sozinho no boot, é preciso habilitar o *lingering* uma única vez:

```bash
loginctl enable-linger <conta-de-rede>
```

Sem isso o portal cai quando a sessão da conta encerra. **Este é o único
comando com root de que o serviço depende no dia a dia** — a unit em si é
instalada pela própria conta, em `~/.config/systemd/user/`, e o restart não
pede privilégio.

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
| base Oracle EBS (`rac04-scan`) | 1521 |
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

## O que a equipe do portal faz depois (sem root)

1. Clonar o código na pasta e criar o venv com `--system-site-packages`.
2. Escrever `data/environment` (só configuração — o modelo está em
   `deploy/environment.servidor-novo`) e criar `data/cofre/` em modo 700.
3. Gravar os segredos: `python3 scripts/cofre.py definir NOME`.
4. Conferir: `python3 scripts/cofre.py conferir` — inclusive que a cifra em
   uso é `fernet`, e não a de contingência.
5. Instalar a unit em `~/.config/systemd/user/` e habilitar o serviço.
6. Restaurar os dados do servidor antigo e validar as telas.

## Conferência final

```bash
systemctl --user is-active portal-spare
curl -s -o /dev/null -w '%{http_code}\n' http://localhost:8901/
curl -s -o /dev/null -w '%{http_code}\n' http://localhost:8901/consulta-times
journalctl --user -u portal-spare -n 30 --no-pager

# nenhuma senha em texto claro na configuração:
grep -iE 'pass|senha|secret' /var/www/vcreports/portal-spare/data/environment
# só deve aparecer o marcador @cofre:NOME@

ls -ld /var/www/vcreports/portal-spare/data/cofre    # drwx------
```
