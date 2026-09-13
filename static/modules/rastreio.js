/* Correios — rastreio de objetos em lote.
   O encerramento de chamados entregues saiu daqui: quem faz isso hoje é a
   rotina de Automações (Parâmetros → Automações) e a tela do ServiceNow. */
(function () {
    var S = window.SPARE;

    function render(content, sub) {
        content.innerHTML = '';
        renderRastreios(content, S);
    }

    /* ── Barra de progresso reutilizável ──────────────────────────── */
    function novoProgresso(S) {
        var wrap = S.el('div', { style: 'margin:12px 0;display:none' });
        wrap.innerHTML =
            '<div style="display:flex;justify-content:space-between;font-size:.85rem;' +
            'color:var(--text-secondary);margin-bottom:4px">' +
            '<span class="pg-label">Processando...</span><span class="pg-pct">0%</span></div>' +
            '<div style="height:10px;background:var(--bg-secondary,#eee);border-radius:6px;overflow:hidden">' +
            '<div class="pg-fill" style="height:100%;width:0%;background:#3b82f6;transition:width .2s"></div></div>';
        return {
            el: wrap,
            iniciar: function (label) {
                wrap.style.display = 'block';
                wrap.querySelector('.pg-label').textContent = label || 'Processando...';
                wrap.querySelector('.pg-pct').textContent = '0%';
                wrap.querySelector('.pg-fill').style.width = '0%';
            },
            atualizar: function (feito, total, label) {
                var pct = total ? Math.round((feito / total) * 100) : 0;
                wrap.querySelector('.pg-pct').textContent = pct + '% (' + feito + '/' + total + ')';
                wrap.querySelector('.pg-fill').style.width = pct + '%';
                if (label) wrap.querySelector('.pg-label').textContent = label;
            },
            terminar: function (label) {
                wrap.querySelector('.pg-fill').style.width = '100%';
                wrap.querySelector('.pg-pct').textContent = '100%';
                if (label) wrap.querySelector('.pg-label').textContent = label;
                setTimeout(function () { wrap.style.display = 'none'; }, 800);
            }
        };
    }

    /* ── Aba Rastreios (consulta em lote) ─────────────────────────── */
    function renderRastreios(content, S) {
        var card = S.el('div', { className: 'card' });
        var body = S.el('div', { className: 'card-body' });
        body.innerHTML =
            '<h1 class="page-title">Rastreios</h1>' +
            '<p style="color:var(--text-secondary);margin:0 0 12px">' +
            'Cole um ou vários códigos de rastreio (um por linha ou separados por espaço/vírgula) ' +
            'e consulte o status de todos de uma vez.</p>';

        var ta = S.el('textarea', {
            id: 'rt-cods', className: 'form-control',
            placeholder: 'AD852897611BR\nAD528273626BR\n...',
            style: 'width:100%;min-height:110px;text-transform:uppercase;font-family:monospace'
        });
        body.appendChild(ta);

        var row = S.el('div', { style: 'display:flex;gap:8px;flex-wrap:wrap;margin-top:8px' });
        var btn = S.el('button', { className: 'btn btn-primary', textContent: 'Rastrear todos' });
        var testBtn = S.el('button', { className: 'btn btn-outline', textContent: 'Testar conexão' });
        row.appendChild(btn);
        row.appendChild(testBtn);
        body.appendChild(row);

        var prog = novoProgresso(S);
        body.appendChild(prog.el);

        var result = S.el('div', { id: 'rt-result', style: 'margin-top:12px' });
        body.appendChild(result);

        card.appendChild(body);
        content.appendChild(card);

        function statusDoObjeto(d) {
            if (!d.encontrado) return { txt: 'Não encontrado', cor: '#6b7280' };
            if (d.entrega && d.entrega.entregue) return { txt: 'Entregue', cor: '#16a34a' };
            return { txt: 'Em trânsito', cor: '#2563eb' };
        }

        function cardResultado(cod, d, erro) {
            var st = erro ? { txt: 'Erro', cor: '#dc2626' } : statusDoObjeto(d);
            var ultimo = '';
            if (!erro && d.eventos && d.eventos.length) {
                var ev = d.eventos[0];
                ultimo = (ev.descricao || '') + (ev.local ? ' — ' + ev.local : '') +
                    (ev.data ? ' (' + ev.data + ')' : '');
            }
            var rec = '';
            if (!erro && d.entrega && d.entrega.entregue && d.entrega.recebedor_nome) {
                rec = 'Recebedor: ' + d.entrega.recebedor_nome;
            }
            var det = erro ? erro : (ultimo || rec || '—');
            return '<div style="display:flex;gap:12px;align-items:flex-start;padding:8px 10px;' +
                'border:1px solid var(--border,#eee);border-radius:8px;margin-bottom:6px">' +
                '<span style="font-family:monospace;min-width:130px;font-weight:600">' + S.esc(cod) + '</span>' +
                '<span style="min-width:110px;font-weight:600;color:' + st.cor + '">' + S.esc(st.txt) + '</span>' +
                '<span style="color:var(--text-secondary);font-size:.88rem;flex:1">' + S.esc(det) +
                (rec && ultimo ? '<br>' + S.esc(rec) : '') + '</span></div>';
        }

        function parseCodigos() {
            var brutos = (ta.value || '').toUpperCase().split(/[\s,;]+/);
            var vistos = {}, out = [];
            brutos.forEach(function (c) {
                c = c.trim();
                if (c.length >= 10 && !vistos[c]) { vistos[c] = 1; out.push(c); }
            });
            return out;
        }

        async function rastrearTodos() {
            var cods = parseCodigos();
            if (!cods.length) { S.toast('Cole ao menos um código válido.', 'warning'); return; }
            btn.disabled = true; btn.textContent = 'Consultando...';
            result.innerHTML = '';
            prog.iniciar('Consultando ' + cods.length + ' objeto(s)...');
            var html = '';
            for (var i = 0; i < cods.length; i++) {
                var cod = cods[i];
                try {
                    var d = await S.api('/servicenow/correios/rastrear/' + encodeURIComponent(cod));
                    html += cardResultado(cod, d, null);
                } catch (err) {
                    html += cardResultado(cod, null, err.message || 'falha');
                }
                result.innerHTML = html;
                prog.atualizar(i + 1, cods.length);
            }
            prog.terminar('Concluído: ' + cods.length + ' consultado(s).');
            btn.disabled = false; btn.textContent = 'Rastrear todos';
        }

        btn.onclick = rastrearTodos;

        testBtn.onclick = function () {
            testBtn.disabled = true; testBtn.textContent = 'Testando...';
            result.innerHTML = '<div class="spinner-inline"><span class="spinner spinner-sm"></span> Testando autenticação...</div>';
            S.api('/servicenow/correios/test', { method: 'POST' })
                .then(function (d) {
                    result.innerHTML = '<pre style="white-space:pre-wrap;word-break:break-word;' +
                        'font-size:.8rem;background:var(--bg-secondary);padding:12px;border-radius:8px;' +
                        'max-height:480px;overflow:auto">' + S.esc(JSON.stringify(d, null, 2)) + '</pre>';
                })
                .catch(function (err) {
                    result.innerHTML = '<div style="color:#dc2626">' + S.esc(err.message) + '</div>';
                })
                .finally(function () { testBtn.disabled = false; testBtn.textContent = 'Testar conexão'; });
        };
    }

    window.SPARE_MODULES = window.SPARE_MODULES || {};
    window.SPARE_MODULES.rastreio = { render: render };
})();
