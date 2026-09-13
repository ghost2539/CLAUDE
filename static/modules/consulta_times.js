/* ================================================================
   Módulo: Acesso Consulta Times

   Quem administra libera logins de rede para o espaço /consulta-times
   e vê a trilha de acesso. Só aparece para quem tem nível admin.
   ================================================================ */
window.SPARE_MODULES = window.SPARE_MODULES || {};

(function () {
    var S = null;

    window.SPARE_MODULES.consulta_times = {
        render: function (c) {
            S = window.SPARE;
            desenhar(c);
        }
    };

    async function desenhar(c) {
        c.innerHTML = '<div class="spinner-inline"><span class="spinner spinner-sm"></span> Carregando...</div>';
        var eu;
        try { eu = await S.api('/consulta-times/eu'); } catch (e) { return erro(c, e); }
        c.innerHTML = '';
        c.appendChild(S.el('h2', { className: 'page-title', textContent: 'Acesso Consulta Times' }));
        if (eu.nivel !== 'admin') {
            c.appendChild(S.el('div', { className: 'alert alert-warning', textContent: 'Só quem administra o módulo libera acessos.' }));
            return;
        }
        var d;
        try { d = await S.api('/consulta-times/liberacoes'); } catch (e) { return erro(c, e); }

        var form = S.el('div', { className: 'card-body form-row-inline' });
        var login = S.el('input', { className: 'form-control form-control-inline', placeholder: 'login de rede', autocomplete: 'off' });
        var nome = S.el('input', { className: 'form-control form-control-inline', placeholder: 'nome (opcional)' });
        var nivel = S.el('select', { className: 'form-control form-control-inline' });
        [['view', 'Consulta'], ['edit', 'Consulta + ServiceNow'], ['admin', 'Administrar']].forEach(function (o) {
            nivel.appendChild(S.el('option', { value: o[0], textContent: o[1] }));
        });
        form.appendChild(login); form.appendChild(nome); form.appendChild(nivel);
        form.appendChild(S.el('button', { className: 'btn btn-primary', textContent: 'Liberar', onClick: async function () {
            if (!login.value.trim()) { S.toast('Informe o login.', 'warning'); return; }
            try {
                await S.api('/consulta-times/liberacoes', { method: 'POST', body: { login: login.value.trim(), nome: nome.value.trim(), nivel: nivel.value } });
                S.toast('Acesso liberado.', 'success'); desenhar(c);
            } catch (e) { S.toast(e.message, 'error'); }
        } }));
        c.appendChild(cartao('Liberar login', form));

        var corpo = S.el('div', { className: 'card-body' });
        if (!d.liberacoes.length) corpo.appendChild(S.el('p', { className: 'sep-vazio', textContent: 'Ninguém liberado ainda.' }));
        else corpo.appendChild(S.table([
            { key: 'login', label: 'Login' }, { key: 'nome', label: 'Nome' },
            { key: 'nivel', label: 'Nível', render: function (v) { return ({ view: 'Consulta', edit: 'Consulta + ServiceNow', admin: 'Administrar' })[v] || v; } },
            { key: 'criado_por', label: 'Liberado por' },
            { key: 'login', label: '', render: function (v) {
                return S.el('button', { className: 'btn btn-sm btn-outline-danger', textContent: 'Revogar', onClick: async function () {
                    try { await S.api('/consulta-times/liberacoes/' + encodeURIComponent(v), { method: 'DELETE' }); S.toast('Revogado.', 'success'); desenhar(c); }
                    catch (e) { S.toast(e.message, 'error'); }
                } });
            } }
        ], d.liberacoes));
        c.appendChild(cartao('Liberados', corpo));

        // Listas das telas do ServiceNow deste espaço
        try {
            var lc = await S.api('/consulta-times/gestao-ativos');
            var cfg = S.el('div', { className: 'card-body' });
            function area(id, rotulo, lista) {
                return '<div class="form-group"><label for="' + id + '">' + rotulo + '</label>' +
                    '<textarea id="' + id + '" class="form-control" rows="3" placeholder="um por linha">' + S.esc((lista || []).join('\n')) + '</textarea></div>';
            }
            cfg.innerHTML = '<div class="form-grid cols-2">' + area('ct-est', 'Estoques', lc.estoques) +
                area('ct-cor', 'Corredores e espaços', lc.corredores) + area('ct-ano', 'Anotações sugeridas', lc.anotacoes) + '</div>';
            cfg.appendChild(S.el('button', { className: 'btn btn-primary mt-2', textContent: 'Salvar', onClick: async function () {
                var linhas = function (id) { return document.getElementById(id).value.split('\n').map(function (x) { return x.trim(); }).filter(Boolean); };
                try { await S.api('/consulta-times/gestao-ativos', { method: 'PUT', body: { estoques: linhas('ct-est'), corredores: linhas('ct-cor'), anotacoes: linhas('ct-ano') } }); S.toast('Configuração salva.', 'success'); }
                catch (e) { S.toast(e.message, 'error'); }
            } }));
            c.appendChild(cartao('Estoques, corredores e anotações (Entrada / Saída / Movimentação)', cfg));
        } catch (e) { /* sem permissão de leitura: não mostra */ }

        var ac = S.el('div', { className: 'card-body' });
        try {
            var a = await S.api('/consulta-times/acessos?limit=100');
            ac.appendChild(S.table([
                { key: 'quando', label: 'Quando', render: function (v) { return v ? new Date(v).toLocaleString('pt-BR') : ''; } },
                { key: 'usuario', label: 'Usuário' }, { key: 'acao', label: 'Ação' }, { key: 'detalhe', label: 'Detalhe' }, { key: 'ip', label: 'IP' }
            ], a.acessos));
        } catch (e) { ac.appendChild(S.el('div', { className: 'alert alert-danger', textContent: e.message })); }
        c.appendChild(cartao('Trilha de acesso', ac));
    }

    function erro(c, e) { c.innerHTML = ''; c.appendChild(S.el('div', { className: 'alert alert-danger', textContent: e.message })); }
    function cartao(t, corpo) {
        var card = S.el('div', { className: 'card mb-3' });
        card.appendChild(S.el('div', { className: 'card-header', textContent: t })); card.appendChild(corpo); return card;
    }
})();
