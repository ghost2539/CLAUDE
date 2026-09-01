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

    window.SPARE_MODULES = window.SPARE_MODULES || {};
    window.SPARE_MODULES.rastreio = { render: render };
})();
