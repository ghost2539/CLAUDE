# Migração Portal SPARE v1 → v2

## Resumo das mudanças

### Segurança
- **API Keys/Secrets**: removidos do código, lidos apenas de variáveis de ambiente
- **Cookies seguros**: httpOnly, SameSite=Lax, flag Secure quando HTTPS ativo
- **Hash de senhas**: bcrypt (já existia, mantido)
- **Rate limiting**: 5 tentativas de login/minuto, 120 requisições API/minuto por IP
- **Bot protection**: bloqueio de user-agents de ferramentas de ataque
- **Security headers**: CSP, X-Frame-Options, X-Content-Type-Options, HSTS
- **Validação de input**: Pydantic em todos os endpoints POST/PUT
- **Limite de upload**: 50MB por padrão (configurável)
- **API docs desabilitada**: endpoints /api/docs e /api/openapi.json removidos

### Permissões (CORREÇÃO CRÍTICA)
- **Bug corrigido**: a função `get_perms` era sobrescrita no final do portal_web.py, dando acesso total a todos os usuários. Agora lê as permissões reais do banco.
- **Matriz por módulo**: cada usuário tem permissões granulares (view/create/edit/export/admin) por módulo (consulta/recebimento/reparos/status/parametros)
- **Admin inicial**: apenas o login configurado em INITIAL_ADMIN_LOGIN tem acesso total

### Modularização
- **Backend**: cada módulo é um router FastAPI separado em `routers/`
- **Frontend**: cada menu carrega seu JS sob demanda (lazy loading)
- **Código morto removido**: módulos v3, v4, v5, v70, v71, v72, backups, e arquivos duplicados eliminados

### Desempenho
- Cada tela carrega apenas o JS/CSS necessário
- Queries otimizadas com limits
- Middleware stack enxuto

## Passos de migração

### 1. Preparar o servidor
```bash
# O portal v1 continua na porta 8502 — não será afetado
# O v2 será instalado na porta 8901

# Copiar o projeto para o servidor
scp -r portal-spare-v2/ usuario@servidor:/tmp/

# No servidor
cd /tmp/portal-spare-v2
sudo bash deploy/install.sh
```

### 2. Configurar o ambiente
```bash
sudo nano /etc/portal_operacoes_spare/environment
# Ajustar:
#   DATABASE_URL (mesma base do v1 — compatível)
#   INITIAL_ADMIN_LOGIN
#   EBS_LOGIN_URL e EBS_SEARCH_URL (URLs reais)
```

### 3. Configurar credenciais EBS
```bash
# Criar arquivos de credencial protegidos
echo "USUARIO_EBS" | sudo tee /etc/portal_operacoes_spare/credentials/ebs_public_username
echo "SENHA_EBS" | sudo tee /etc/portal_operacoes_spare/credentials/ebs_public_password
sudo chmod 600 /etc/portal_operacoes_spare/credentials/*
```

### 4. (Opcional) HTTPS com certificado autoassinado
```bash
sudo bash deploy/generate-cert.sh portalspare.local
# Adicionar ao environment:
#   SSL_CERTFILE=/etc/portal_operacoes_spare/ssl/portal.crt
#   SSL_KEYFILE=/etc/portal_operacoes_spare/ssl/portal.key
```

### 5. (Opcional) Acesso por nome ao invés de IP
No DNS interno ou no arquivo hosts de cada máquina:
```
10.115.30.154  portalspare.local
```
Acesso: `https://portalspare.local:8901`

### 6. Iniciar o serviço
```bash
sudo systemctl start portal_spare.service
sudo systemctl status portal_spare.service
# Verificar logs:
sudo journalctl -u portal_spare.service -f
```

### 7. Validar permissões
Após o primeiro acesso com o admin:
1. Ir em Parâmetros → Usuários e Permissões
2. Configurar acesso por módulo para cada usuário
3. Usuários AD serão registrados no primeiro login

## Compatibilidade com banco de dados
O v2 usa as **mesmas tabelas** do v1. A migração é transparente — ambas as versões podem rodar simultaneamente apontando para o mesmo banco.

## Portas
| Serviço | Porta |
|---------|-------|
| (existente) | 8000 |
| (existente) | 8501 |
| Portal SPARE v1 | 8502 |
| (existente) | 8503 |
| (existente) | 8999 |
| **Portal SPARE v2** | **8901** |
