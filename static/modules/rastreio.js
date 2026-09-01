/* Rastreio Correios avulso — consulta direta, sem ServiceNow.
   Serve para testar a API dos Correios isoladamente. */
(function () {
    var S = window.SPARE;

    function render(content) {
        content.innerHTML = '';

        var card = S.el('div', { className: 'card' });
        var body = S.el('div', { className: 'card-body' });
        body.innerHTML =
            '<h1 class="page-title">Rastreio Correios</h1>' +
            '<p style="color:var(--text-secondary);margin:0 0 12px">' +
            'Consulta avulsa de um objeto pelos Correios. Não depende do ServiceNow.</p>';

        var row = S.el('div', { style: 'display:flex;gap:8px;flex-wrap:wrap;align-items:center' });
        var input = S.el('input', {
            id: 'rt-cod', className: 'form-control',
            placeholder: 'Código de rastreio (ex: AD852897611BR)',
            style: 'max-width:320px;text-transform:uppercase'
        });
        var btn = S.el('button', { className: 'btn btn-primary', textContent: 'Rastrear' });
        var testBtn = S.el('button', {
            className: 'btn btn-outline', textContent: 'Testar conexão',
            style: 'margin-left:4px'
        });
        row.appendChild(input);
        row.appendChild(btn);
        row.appendChild(testBtn);
        body.appendChild(row);

        var result = S.el('div', { id: 'rt-result', style: 'margin-top:16px' });
        body.appendChild(result);

        card.appendChild(body);
        content.appendChild(card);

        renderEncerramento(content, S);

        function erroBox(titulo, detalhe) {
            result.innerHTML =
                '<div style="padding:12px;border:1px solid rgba(220,38,38,.3);' +
                'background:rgba(220,38,38,.08);border-radius:8px">' +
                '<div style="font-weight:600;color:#dc2626;margin-bottom:6px">' + S.esc(titulo) + '</div>' +
                '<pre style="white-space:pre-wrap;word-break:break-word;margin:0;font-size:.82rem;color:var(--text-secondary)">' +
                S.esc(detalhe || '') + '</pre></div>';
        }

        function rastrear() {
            var cod = (input.value || '').trim().toUpperCase();
            if (!cod || cod.length < 10) { S.toast('Informe um código válido.', 'warning'); return; }
            btn.disabled = true; btn.textContent = 'Consultando...';
            result.innerHTML = '<div class="spinner-inline"><span class="spinner spinner-sm"></span> Consultando Correios...</div>';
            S.api('/servicenow/correios/rastrear/' + encodeURIComponent(cod))
                .then(function (d) {
                    if (!d.encontrado) {
                        erroBox('Objeto não encontrado', d.mensagem || 'Sem eventos para este código.');
                        return;
                    }
                    var html = '<div style="font-size:1rem;font-weight:600;margin-bottom:12px">' +
                        S.esc(cod) + (d.tipo_nome ? ' — ' + S.esc(d.tipo_nome) : '') + '</div>';
                    var evs = d.eventos || [];
                    if (!evs.length) {
                        html += '<div style="color:var(--text-secondary)">Sem eventos.</div>';
                    }
                    for (var i = 0; i < evs.length; i++) {
                        var ev = evs[i];
                        var dtStr = ev.data || '';
                        try {
                            var dt = new Date(ev.data);
                            if (!isNaN(dt)) {
                                dtStr = dt.toLocaleDateString('pt-BR') + ' ' +
                                    dt.toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' });
                            }
                        } catch (_) { }
                        html += '<div style="position:relative;margin-left:6px;padding-left:24px;padding-bottom:18px;' +
                            (i < evs.length - 1 ? 'border-left:2px solid #333' : '') + '">' +
                            '<div style="position:absolute;left:-7px;top:2px;width:14px;height:14px;border-radius:50%;background:#3b82f6"></div>' +
                            '<div style="font-weight:600;font-size:.95rem">' + S.esc(ev.descricao || '') + '</div>' +
                            (ev.detalhe ? '<div style="color:var(--text-secondary);font-size:.85rem">' + S.esc(ev.detalhe) + '</div>' : '') +
                            '<div style="color:var(--text-secondary);font-size:.8rem;margin-top:2px">' +
                            (ev.local ? S.esc(ev.local) + ' — ' : '') + dtStr + '</div>' +
                        '</div>';
                    }
                    if (d.entrega && d.entrega.entregue) {
                        var e = d.entrega;
                        html += '<div style="margin-top:8px;padding:12px;background:rgba(22,163,74,.08);' +
                            'border:1px solid rgba(22,163,74,.25);border-radius:8px;font-size:.9rem">' +
                            '<div style="font-weight:600;color:#16a34a;margin-bottom:4px">Entregue</div>' +
                            (e.recebedor_nome ? '<div>Recebedor: <strong>' + S.esc(e.recebedor_nome) + '</strong></div>' : '') +
                            (e.recebedor_documento ? '<div>Documento: ' + S.esc(e.recebedor_documento) + '</div>' : '') +
                            (e.local_entrega ? '<div>Local: ' + S.esc(e.local_entrega) + '</div>' : '') +
                        '</div>';
                    }
                    result.innerHTML = html;
                })
                .catch(function (err) { erroBox('Falha no rastreio', err.message); })
                .finally(function () { btn.disabled = false; btn.textContent = 'Rastrear'; });
        }

        btn.onclick = rastrear;
        input.addEventListener('keydown', function (e) {
            if (e.key === 'Enter') { e.preventDefault(); rastrear(); }
        });

        testBtn.onclick = function () {
            testBtn.disabled = true; testBtn.textContent = 'Testando...';
            result.innerHTML = '<div class="spinner-inline"><span class="spinner spinner-sm"></span> Testando autenticação...</div>';
            S.api('/servicenow/correios/test', { method: 'POST' })
                .then(function (d) {
                    result.innerHTML = '<pre style="white-space:pre-wrap;word-break:break-word;' +
                        'font-size:.8rem;background:var(--bg-secondary);padding:12px;border-radius:8px;' +
                        'max-height:480px;overflow:auto">' + S.esc(JSON.stringify(d, null, 2)) + '</pre>';
                })
                .catch(function (err) { erroBox('Falha no teste', err.message); })
                .finally(function () { testBtn.disabled = false; testBtn.textContent = 'Testar conexão'; });
        };
    }

    /* ── Encerramento de chamados entregues (escreve no ServiceNow) ── */
    function renderEncerramento(content, S) {
        var card = S.el('div', { className: 'card', style: 'margin-top:16px' });
        var body = S.el('div', { className: 'card-body' });
        body.innerHTML =
            '<h2 style="margin:0 0 4px;font-size:1.1rem">Encerramento de chamados entregues</h2>' +
            '<p style="color:var(--text-secondary);margin:0 0 12px;font-size:.9rem">' +
            'Lista chamados On Hold/In Progress de coletor/sled cujo objeto já foi entregue. ' +
            'O encerramento <strong>altera o ServiceNow</strong> — revise antes de confirmar.</p>';

        var btn = S.el('button', { className: 'btn btn-outline', textContent: 'Buscar candidatos' });
        body.appendChild(btn);
        var out = S.el('div', { id: 'enc-out', style: 'margin-top:12px' });
        body.appendChild(out);
        card.appendChild(body);
        content.appendChild(card);

        function carregar() {
            btn.disabled = true; btn.textContent = 'Buscando...';
            out.innerHTML = '<div class="spinner-inline"><span class="spinner spinner-sm"></span> Analisando fila...</div>';
            S.api('/servicenow/encerramento/candidatos')
                .then(function (d) {
                    var els = d.elegiveis || [];
                    if (!els.length) {
                        out.innerHTML = '<div style="color:var(--text-secondary)">' +
                            'Nenhum candidato. Analisados: ' + (d.total_analisados || 0) + '.</div>';
                        return;
                    }
                    var h = '<div style="margin-bottom:8px;color:var(--text-secondary)">' +
                        els.length + ' candidato(s) (de ' + d.total_analisados + ' analisados):</div>' +
                        '<table class="table"><thead><tr><th>Número</th><th>Subcat.</th>' +
                        '<th>Rastreio</th><th>Entrega</th><th></th></tr></thead><tbody>';
                    els.forEach(function (c) {
                        var ent = c.entrega || {};
                        h += '<tr>' +
                            '<td>' + S.esc(c.number) + '</td>' +
                            '<td>' + S.esc(c.subcategory) + '</td>' +
                            '<td>' + S.esc(c.tracking) + '</td>' +
                            '<td>' + S.esc((ent.data || '') + ' ' + (ent.recebedor_nome || '')) + '</td>' +
                            '<td><button class="btn btn-sm btn-primary" data-sid="' + S.esc(c.sys_id) +
                            '" data-num="' + S.esc(c.number) + '">Encerrar</button></td>' +
                        '</tr>';
                    });
                    h += '</tbody></table>';
                    out.innerHTML = h;
                    out.querySelectorAll('button[data-sid]').forEach(function (b) {
                        b.onclick = function () { encerrar(b.getAttribute('data-sid'), b.getAttribute('data-num'), b); };
                    });
                })
                .catch(function (err) {
                    out.innerHTML = '<div style="color:#dc2626">' + S.esc(err.message) + '</div>';
                })
                .finally(function () { btn.disabled = false; btn.textContent = 'Buscar candidatos'; });
        }

        function encerrar(sysId, numero, b) {
            if (!confirm('Encerrar o chamado ' + numero + ' no ServiceNow?\n\n' +
                'Isto muda o estado para Resolvido e preenche a closure information. ' +
                'Ação real, não pode ser desfeita pelo portal.')) return;
            b.disabled = true; b.textContent = 'Encerrando...';
            S.api('/servicenow/encerramento/executar', {
                method: 'POST', body: { sys_id: sysId, confirmar: true }
            })
                .then(function (r) {
                    S.toast('Chamado ' + (r.encerrado || numero) + ' encerrado.', 'success');
                    b.textContent = 'Encerrado ✓';
                })
                .catch(function (err) {
                    S.toast(err.message || 'Falha ao encerrar.', 'error');
                    b.disabled = false; b.textContent = 'Encerrar';
                });
        }

        btn.onclick = carregar;
    }

    window.SPARE_MODULES = window.SPARE_MODULES || {};
    window.SPARE_MODULES.rastreio = { render: render };
})();
