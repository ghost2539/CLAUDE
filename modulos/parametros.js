/* ================================================================
   Module: Parâmetros — as abas que todo usuário recebe

   Este arquivo desce para todo usuário, porque a aba "Minha conta" é
   de todos. Por isso as telas de administração não moram mais aqui:
   estão em `parametros_admin.js`, pedido só quando uma aba de admin é
   aberta — e só por quem é admin.
   ================================================================ */
window.SPARE_MODULES = window.SPARE_MODULES || {};
window.SPARE_MODULES.parametros = {

    render(container, sub) {
        var S = window.SPARE;
        var u = S.user();

        container.classList.add('parameters-module');

        var allTabs = [
            ['visual',          'Visual'],
            ['locais',          'Locais'],
            ['classificacoes',  'Classificações'],
            ['permissoes',      'Usuários e Permissões'],
            ['sequencias',      'Sequências'],
            ['config-modulos',  'Configuração Módulos'],
            ['separacao',       'Ciclo do ativo'],
            ['cofre',           'Cofre de segredos'],
            ['base-ebs',        'Base EBS'],
            ['monitoramento',   'Monitoramento'],
            ['acessos',         'Acessos & Alertas'],
            ['dashboards',      'Dashboards'],
            ['conta',           'Minha conta']
        ];

        // Sobrou uma aba de todos: a Minha conta. Todo o resto é de admin —
        // e é esta lista que também decide o que vem do arquivo de admin,
        // logo abaixo.
        var adminOnly = ['visual', 'locais', 'classificacoes', 'permissoes',
                         'sequencias', 'config-modulos', 'separacao', 'monitoramento',
                         'cofre', 'base-ebs', 'acessos', 'dashboards'];
        var visibleTabs = allTabs.filter(function (x) {
            return u.is_admin || adminOnly.indexOf(x[0]) === -1;
        });

        sub = sub || 'conta';
        S.tabs(visibleTabs, sub, 'parametros');

        container.innerHTML =
            '<div class="spinner-inline"><span class="spinner spinner-sm"></span> Carregando...</div>';

        // A mesma lista que esconde a aba decide o que vem do outro arquivo:
        // duas listas se desencontrariam, e o desencontro aqui seria justo
        // entregar tela de admin a quem não é. Quem não é admin para nesta
        // linha — nem a requisição do arquivo chega a sair, mesmo que o
        // endereço venha digitado na URL (#parametros/base-ebs).
        if (adminOnly.indexOf(sub) !== -1) {
            if (!u.is_admin) {
                container.innerHTML =
                    '<div class="alert alert-danger"><strong>Acesso negado.</strong><br>' +
                    'Esta tela é da administração do portal.</div>';
                return;
            }
            _pCarregarAdmin().then(function (telas) {
                var render = telas[sub];
                if (!render) throw new Error('Tela de administração desconhecida: ' + sub);
                return render(container, S);
            }).catch(function (e) {
                container.innerHTML =
                    '<div class="alert alert-danger"><strong>Falha ao carregar.</strong><br>' +
                    S.esc(e.message || e) + '</div>';
            });
            return;
        }

        // Fora do adminOnly só existe a Minha conta — um mapa de um item só
        // seria cerimônia para esconder isso.
        Promise.resolve(renderAccount(container, S)).catch(function (e) {
            container.innerHTML =
                '<div class="alert alert-danger"><strong>Falha ao carregar.</strong><br>' +
                S.esc(e.message || e) + '</div>';
        });
    }

};

/* ── Helper: field builder ──────────────────────────────────────── */
function _pField(label, id, value, type) {
    var S = window.SPARE;
    var d = S.el('div', { className: 'form-group' });
    d.innerHTML = '<label>' + S.esc(label) + '</label>' +
        '<input id="' + id + '" type="' + (type || 'text') + '" class="form-control" ' +
        'value="' + S.esc(value || '') + '">';
    return d;
}

/* ── Minha conta ────────────────────────────────────────────────── */
function renderAccount(c, S) {
    var u = S.user();
    c.innerHTML =
        '<h1 class="page-title">Minha conta</h1>' +
        '<div class="card">' +
            '<div class="card-body">' +
                '<p><span class="text-muted">Usuário:</span> <strong>' + S.esc(u.username) + '</strong></p>' +
                '<p><span class="text-muted">Perfil:</span> <strong>' + S.esc(u.role || '') + '</strong></p>' +
                // Sem troca de senha: o acesso é só por Logon AD (SSO).
                '<div class="form-grid cols-2">' +
                    '<div class="form-group"><label>Nome de exibição</label>' +
                        '<input id="pm-nome" class="form-control" maxlength="120" value="' +
                        S.esc(u.display_name || '') + '"></div>' +
                '</div>' +
                '<button id="pm-nome-save" class="btn btn-primary mt-2">Salvar nome</button>' +
            '</div>' +
        '</div>';

    document.getElementById('pm-nome-save').onclick = async function () {
        var nome = document.getElementById('pm-nome').value.trim();
        try {
            var d = await S.api('/auth/meu-nome', { method: 'POST', body: { display_name: nome } });
            u.display_name = d.display_name;
            // o cabeçalho mostra o nome: atualiza sem precisar recarregar
            var alvo = document.getElementById('topbar-user-name');
            if (alvo) alvo.textContent = d.display_name;
            var av = document.getElementById('topbar-user-avatar');
            if (av && d.display_name) av.textContent = d.display_name[0].toUpperCase();
            S.toast('Nome atualizado.', 'success');
        } catch (e) {
            S.toast(e.message, 'error');
        }
    };
}

/* ── Telas de administração, sob demanda ──────────────────────────
   Mesmo desenho do `gestao_ativos.js`: uma tag <script> embrulhada em
   Promise. A Promise fica guardada para o arquivo ser pedido uma vez
   só na sessão — abrir e reabrir as abas de admin não repete o
   download. */
var _pAdminCarregando = null;

function _pCarregarAdmin() {
    if (window.SPARE_PARAMETROS_ADMIN) return Promise.resolve(window.SPARE_PARAMETROS_ADMIN);
    if (_pAdminCarregando) return _pAdminCarregando;
    _pAdminCarregando = new Promise(function (ok, falhou) {
        var s = document.createElement('script');
        s.src = _pAppBase() + '/modulos/parametros_admin.js?v=' + Date.now();
        s.onload = function () {
            // A rota é autenticada: ela pode responder 200 com outra coisa
            // (a tela de login, por exemplo) e o onload dispararia igual.
            // Quem diz que o módulo veio é o mapa das abas.
            if (!window.SPARE_PARAMETROS_ADMIN) {
                _pAdminCarregando = null;
                falhou(new Error('As telas de administração não chegaram. ' +
                                 'Entre de novo no portal e tente outra vez.'));
                return;
            }
            ok(window.SPARE_PARAMETROS_ADMIN);
        };
        s.onerror = function () {
            // Sem zerar aqui, a falha ficaria grudada na Promise e nenhuma
            // nova tentativa sairia até recarregar a página inteira.
            _pAdminCarregando = null;
            falhou(new Error('Não foi possível carregar as telas de administração.'));
        };
        document.head.appendChild(s);
    });
    return _pAdminCarregando;
}

/* Prefixo do portal quando ele é servido num subcaminho do proxy
   (/portal-spare): o main.py injeta <meta name="app-base">. Sem isto o
   <script> pediria o arquivo na raiz do domínio e tomaria 404. */
function _pAppBase() {
    var m = document.querySelector('meta[name="app-base"]');
    return (m && m.content ? m.content : '').replace(/\/+$/, '');
}
