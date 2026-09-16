/* ================================================================
   Módulo: Acesso Consulta Times

   Quem administra o espaço /consulta-times faz aqui quatro coisas, e
   só estas:

     1. libera um usuário de rede, com nome e nível de acesso;
     2. vê quem está liberado e quem liberou;
     3. define os estoques, corredores e espaços que as telas de
        Entrada, Saída e Movimentação do espaço oferecem;
     4. lê a trilha de acesso.

   A tela é montada de uma vez, com todos os dados já na mão. Montar
   por partes, entre `await`s, duplicava blocos quando duas
   renderizações corriam juntas. Pelo mesmo motivo os elementos são
   guardados em variáveis, nunca procurados por id: com duas telas na
   página, `getElementById` acha a errada.
   ================================================================ */
window.SPARE_MODULES = window.SPARE_MODULES || {};

(function () {
    var S = null;

    var NIVEIS = [
        ['view',  'Consulta'],
        ['edit',  'Consulta + ServiceNow'],
        ['admin', 'Administrar']
    ];

    function rotuloNivel(v) {
        for (var i = 0; i < NIVEIS.length; i++) {
            if (NIVEIS[i][0] === v) return NIVEIS[i][1];
        }
        return v || '';
    }

    function quando(v) {
        return v ? new Date(v).toLocaleString('pt-BR') : '';
    }

    window.SPARE_MODULES.consulta_times = {
        render: function (c) { S = window.SPARE; desenhar(c); }
    };

    // ── Montagem ──────────────────────────────────────────────────
    async function desenhar(c) {
        c.innerHTML = '<div class="spinner-inline"><span class="spinner spinner-sm"></span> Carregando…</div>';

        var eu;
        try {
            eu = await S.api('/consulta-times/eu');
        } catch (e) {
            return trocar(c, aviso('alert-danger', e.message));
        }
        if (eu.nivel !== 'admin') {
            return trocar(c, aviso('alert-warning', 'Só quem administra o espaço libera acessos.'));
        }

        // Os três pedidos saem juntos e nenhum derruba os outros: o bloco
        // que falhar mostra o motivo no lugar do conteúdo.
        var res = await Promise.all([
            '/consulta-times/liberacoes',
            '/consulta-times/gestao-ativos',
            '/consulta-times/acessos?limit=100'
        ].map(function (url) {
            return S.api(url).then(
                function (d) { return { dado: d }; },
                function (e) { return { erro: e.message }; }
            );
        }));

        var tela = document.createDocumentFragment();
        tela.appendChild(S.el('h1', { className: 'page-title', textContent: 'Acesso Consulta Times' }));
        tela.appendChild(blocoLiberacao(c));
        tela.appendChild(blocoLiberados(c, res[0]));
        tela.appendChild(blocoListas(res[1]));
        tela.appendChild(blocoTrilha(res[2]));
        trocar(c, tela);
    }

    // ── 1. Liberação de acesso ────────────────────────────────────
    function blocoLiberacao(c) {
        var login = S.el('input', {
            className: 'form-control', autocomplete: 'off', autocapitalize: 'none',
            spellcheck: false, placeholder: 'login da rede'
        });
        var nome = S.el('input', { className: 'form-control', autocomplete: 'off', placeholder: 'opcional' });
        var nivel = S.el('select', { className: 'form-control' });
        NIVEIS.forEach(function (n) {
            nivel.appendChild(S.el('option', { value: n[0], textContent: n[1] }));
        });

        var botao = S.el('button', { className: 'btn btn-primary', textContent: 'Liberar acesso' });
        botao.onclick = async function () {
            var usuario = login.value.trim();
            if (!usuario) { S.toast('Informe o usuário de rede.', 'warning'); login.focus(); return; }
            botao.disabled = true;
            try {
                await S.api('/consulta-times/liberacoes', {
                    method: 'POST',
                    body: { login: usuario, nome: nome.value.trim(), nivel: nivel.value }
                });
                S.toast('Acesso liberado.', 'success');
                desenhar(c);
            } catch (e) {
                S.toast(e.message, 'error');
                botao.disabled = false;
            }
        };

        var corpo = S.el('div', { className: 'card-body' });
        var grade = S.el('div', { className: 'filter-grid' });
        grade.appendChild(campo('Usuário de rede', login));
        grade.appendChild(campo('Nome', nome));
        grade.appendChild(campo('Nível do acesso', nivel));
        corpo.appendChild(grade);
        corpo.appendChild(S.el('div', { className: 'btn-row mt-3' }, [botao]));
        return cartao('Liberação de acesso', corpo);
    }

    // ── 2. Usuários liberados ─────────────────────────────────────
    function blocoLiberados(c, r) {
        var corpo = S.el('div', { className: 'card-body' });
        if (r.erro) {
            corpo.appendChild(aviso('alert-danger', r.erro));
            return cartao('Usuários liberados', corpo);
        }
        corpo.appendChild(S.table([
            { key: 'login', label: 'Usuário de rede' },
            { key: 'nome', label: 'Nome' },
            { key: 'nivel', label: 'Nível', render: rotuloNivel },
            { key: 'criado_por', label: 'Liberado por' },
            { key: 'criado_em', label: 'Liberado em', render: quando },
            { key: 'login', label: '', render: function (v) {
                var b = S.el('button', { className: 'btn btn-sm btn-outline-danger', textContent: 'Revogar' });
                b.onclick = async function () {
                    b.disabled = true;
                    try {
                        await S.api('/consulta-times/liberacoes/' + encodeURIComponent(v), { method: 'DELETE' });
                        S.toast('Acesso revogado.', 'success');
                        desenhar(c);
                    } catch (e) { S.toast(e.message, 'error'); b.disabled = false; }
                };
                return b;
            } }
        ], r.dado.liberacoes));
        return cartao('Usuários liberados', corpo);
    }

    // ── 3. Estoques, corredores e espaços ─────────────────────────
    function blocoListas(r) {
        var corpo = S.el('div', { className: 'card-body' });
        if (r.erro) {
            corpo.appendChild(aviso('alert-danger', r.erro));
            return cartao('Estoques, corredores e espaços', corpo);
        }
        var escolhidos = (r.dado.estoques || []).slice();

        corpo.appendChild(S.el('p', { className: 'text-muted mb-3',
            textContent: 'O que as telas de Entrada, Saída e Movimentação de ativos deste espaço oferecem.' }));

        // Estoque não se digita: vem do ServiceNow (alm_stockroom), para o
        // nome bater exatamente com o do registro.
        var lista = S.el('div', { className: 'ct-estoques' }, [
            S.el('span', { className: 'text-muted', textContent: 'Carregando do ServiceNow…' })
        ]);
        corpo.appendChild(campo('Estoques', lista));

        var corredores = S.el('textarea', {
            className: 'form-control', rows: '4', placeholder: 'um por linha',
            textContent: (r.dado.corredores || []).join('\n')
        });
        corpo.appendChild(S.el('div', { className: 'mt-3' }, [campo('Corredores e espaços', corredores)]));

        var salvar = S.el('button', { className: 'btn btn-primary', textContent: 'Salvar' });
        salvar.onclick = async function () {
            var marcados = Array.prototype.slice
                .call(lista.querySelectorAll('input[type=checkbox]:checked'))
                .map(function (i) { return i.value; });
            var linhas = corredores.value.split('\n')
                .map(function (x) { return x.trim(); })
                .filter(Boolean);
            salvar.disabled = true;
            try {
                await S.api('/consulta-times/gestao-ativos', {
                    method: 'PUT', body: { estoques: marcados, corredores: linhas }
                });
                escolhidos = marcados;
                S.toast('Listas salvas.', 'success');
            } catch (e) { S.toast(e.message, 'error'); }
            salvar.disabled = false;
        };
        corpo.appendChild(S.el('div', { className: 'btn-row mt-3' }, [salvar]));

        S.api('/consulta-times/stockrooms').then(function (d) {
            var todos = (d.estoques || []).slice();
            escolhidos.forEach(function (e) { if (todos.indexOf(e) < 0) todos.push(e); });
            lista.innerHTML = '';
            if (!todos.length) {
                lista.appendChild(S.el('span', { className: 'text-muted', textContent: 'Nenhum estoque disponível.' }));
                return;
            }
            todos.forEach(function (nome) {
                var marca = S.el('input', { type: 'checkbox', value: nome });
                marca.checked = escolhidos.indexOf(nome) >= 0;
                lista.appendChild(S.el('label', { className: 'ct-estoque' },
                    [marca, S.el('span', { textContent: nome })]));
            });
        }, function (e) {
            lista.innerHTML = '';
            lista.appendChild(aviso('alert-danger', e.message));
        });

        return cartao('Estoques, corredores e espaços', corpo);
    }

    // ── 4. Trilha de acesso ───────────────────────────────────────
    function blocoTrilha(r) {
        var corpo = S.el('div', { className: 'card-body' });
        if (r.erro) {
            corpo.appendChild(aviso('alert-danger', r.erro));
        } else {
            corpo.appendChild(S.table([
                { key: 'quando', label: 'Quando', render: quando },
                { key: 'usuario', label: 'Usuário' },
                { key: 'acao', label: 'Ação' },
                { key: 'detalhe', label: 'Detalhe' },
                { key: 'ip', label: 'IP' }
            ], r.dado.acessos));
        }
        return cartao('Trilha de acesso', corpo);
    }

    // ── Peças ─────────────────────────────────────────────────────
    function campo(rotulo, controle) {
        return S.el('div', { className: 'form-group' }, [
            S.el('label', { textContent: rotulo }), controle
        ]);
    }

    function cartao(titulo, corpo) {
        return S.el('div', { className: 'card mb-3' }, [
            S.el('div', { className: 'card-header', textContent: titulo }), corpo
        ]);
    }

    function aviso(classe, texto) {
        return S.el('div', { className: 'alert ' + classe, textContent: texto });
    }

    function trocar(c, conteudo) {
        c.innerHTML = '';
        c.appendChild(conteudo);
    }
})();
