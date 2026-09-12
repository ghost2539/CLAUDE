/* Painel de Obsolescência do Parque.

   Enquanto a coleta do MDM não roda pela primeira vez, a tela explica o
   que falta em vez de mostrar um painel vazio. Quando o coletor passar a
   gravar, é aqui que os indicadores entram — sobre o formato real dos
   dados, levantado com scripts/mdm_airwatch_captura.js. */
(function () {
    'use strict';

    var alvo = document.getElementById('obs-conteudo');

    function esc(s) {
        return String(s == null ? '' : s).replace(/[&<>'"]/g, function (c) {
            return { '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' }[c];
        });
    }

    function pendente() {
        alvo.innerHTML =
            '<div class="obs-aviso">' +
                '<h2>Aguardando a primeira coleta do MDM</h2>' +
                '<p>O painel já está no ar e integrado ao portal, mas ainda não há ' +
                    'dados do parque para exibir: a leitura do MDM de Coletores ainda ' +
                    'não foi ligada.</p>' +
                '<p>Para ligar, faltam três passos:</p>' +
                '<ol>' +
                    '<li>Rodar <code>scripts/mdm_airwatch_captura.js</code> no console do ' +
                        'navegador, com a tela do MDM aberta, para levantar quais dados ela traz.</li>' +
                    '<li>Configurar a coleta em segundo plano sobre os endpoints identificados.</li>' +
                    '<li>Tratar os dados e publicar os indicadores nesta tela.</li>' +
                '</ol>' +
            '</div>';
    }

    function erro(msg) {
        alvo.innerHTML = '<div class="obs-erro">' + esc(msg) + '</div>';
    }

    fetch('/api/obsolescencia/resumo', { credentials: 'include' })
        .then(function (r) {
            if (r.status === 401) { location.href = '/?next=/obsolescencia'; return null; }
            if (!r.ok) throw new Error('Não foi possível carregar o painel (HTTP ' + r.status + ').');
            return r.json();
        })
        .then(function (d) {
            if (!d) return;
            var u = document.getElementById('obs-usuario');
            if (u && d.usuario) u.textContent = d.usuario;
            var f = document.getElementById('obs-fonte');
            if (f && d.fonte) f.textContent = d.fonte;
            if (d.pendente) { pendente(); return; }
            // Espaço reservado: com dados coletados, os indicadores entram aqui.
            pendente();
        })
        .catch(function (e) { erro(e.message); });
})();
