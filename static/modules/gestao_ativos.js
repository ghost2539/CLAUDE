/* ================================================================
   Módulo: Gestão de Ativos

   Reúne o que antes ficava espalhado no menu ServiceNow:
   Entrada, Saída e Movimentação Interna de ativos — mais a
   Obsolescência do parque, que abre em painel próprio.

   As três primeiras telas continuam sendo as mesmas: elas escrevem
   no ServiceNow e moram em `servicenow.js`. Aqui mudou só o lugar no
   menu, então aquele arquivo é carregado sob demanda em vez de o
   código ser duplicado.
   ================================================================ */
window.SPARE_MODULES = window.SPARE_MODULES || {};

var _gaCarregando = null;

window.SPARE_MODULES.gestao_ativos = {

    render: function (container, sub) {
        var S = window.SPARE;
        var TAB_LIST = [
            ['entrada',       'Entrada de Ativos'],
            ['saida',         'Saída de Ativos'],
            ['movimentacao',  'Movimentação Interna'],
            ['obsolescencia', 'Obsolescência']
        ];
        sub = sub || 'entrada';
        S.tabs(TAB_LIST, sub, 'gestao_ativos');

        if (sub === 'obsolescencia') {
            _gaRenderObsolescencia(container, S);
            return;
        }

        container.innerHTML = '<div class="spinner-inline">' +
            '<span class="spinner spinner-sm"></span> Carregando...</div>';

        _gaCarregarTelas().then(function () {
            var handlers = {
                entrada:      window._snRenderUpload,
                saida:        window._snRenderSaida,
                movimentacao: window._snRenderMovInterna
            };
            (handlers[sub] || window._snRenderUpload)(container, S);
        }, function (e) {
            container.innerHTML = '<div class="alert alert-danger">' + S.esc(e.message) + '</div>';
        });
    }

};

/* Carrega sob demanda o arquivo que implementa as telas de ativos. */
function _gaCarregarTelas() {
    if (window._snRenderUpload) return Promise.resolve();
    if (_gaCarregando) return _gaCarregando;
    _gaCarregando = new Promise(function (ok, falhou) {
        var s = document.createElement('script');
        s.src = '/static/modules/servicenow.js?v=' + Date.now();
        s.onload = function () { _gaCarregando = null; ok(); };
        s.onerror = function () {
            _gaCarregando = null;
            falhou(new Error('Não foi possível carregar as telas de ativos.'));
        };
        document.head.appendChild(s);
    });
    return _gaCarregando;
}

/* ================================================================
   Obsolescência — porta de entrada do painel

   O painel é uma tela separada, em tela cheia, para ser apresentada
   sem a moldura do portal. Aqui ficam só a explicação e o botão que
   a abre em outra aba (abrir por botão evita o bloqueio de pop-up).
   ================================================================ */
function _gaRenderObsolescencia(container, S) {
    container.innerHTML =
        '<h1 class="page-title">Obsolescência do Parque</h1>' +
        '<p class="page-subtitle">Painel dedicado, em tela cheia, com a situação ' +
            'de obsolescência dos coletores.</p>' +
        '<div class="card"><div class="card-body">' +
            '<p style="margin:0 0 12px">O painel abre em uma aba separada, fora da ' +
                'moldura do portal, para ser apresentado em reunião. O acesso continua ' +
                'exigindo o login da rede.</p>' +
            '<p style="margin:0 0 16px;color:var(--text-secondary);font-size:.9rem">' +
                'Fonte dos dados: MDM de Coletores (Workspace ONE / AirWatch), coletado ' +
                'em segundo plano pelo portal.</p>' +
            '<button id="ga-obs-abrir" class="btn btn-primary">Abrir painel de Obsolescência</button>' +
        '</div></div>';

    var btn = document.getElementById('ga-obs-abrir');
    if (btn) {
        btn.addEventListener('click', function () {
            window.open('/obsolescencia', '_blank', 'noopener');
        });
    }
}
