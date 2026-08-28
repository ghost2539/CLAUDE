/* ================================================================
   Consulta de Ativos — Standalone App
   ================================================================ */
(function () {
    'use strict';

    var API = '/api';

    // ── DOM helpers ────────────────────────────────────────────────
    function $(sel) { return document.querySelector(sel); }

    function el(tag, attrs, children) {
        var e = document.createElement(tag);
        if (attrs) {
            Object.keys(attrs).forEach(function (k) {
                var v = attrs[k];
                if (k === 'className') e.className = v;
                else if (k === 'textContent') e.textContent = v;
                else if (k.indexOf('on') === 0 && k.length > 2)
                    e.addEventListener(k.slice(2).toLowerCase(), v);
                else e.setAttribute(k, v);
            });
        }
        if (Array.isArray(children)) {
            children.forEach(function (x) { if (x) e.appendChild(x); });
        } else if (children instanceof Node) {
            e.appendChild(children);
        } else if (typeof children === 'string') {
            e.innerHTML = children;
        }
        return e;
    }

    function esc(s) {
        return String(s == null ? '' : s).replace(/[&<>'"]/g, function (c) {
            return { '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' }[c];
        });
    }

    // ── API helper ─────────────────────────────────────────────────
    async function api(path, opts) {
        opts = opts || {};
        var headers = { 'Content-Type': 'application/json' };
        var body = opts.body;
        if (body && typeof body === 'object') body = JSON.stringify(body);
        var url = path.indexOf('http') === 0 ? path : API + path;
        var r = await fetch(url, Object.assign({}, opts, {
            body: body,
            headers: Object.assign(headers, opts.headers || {})
        }));
        if (!r.ok) {
            var d;
            try { d = await r.json(); } catch (_) { d = { detail: r.statusText }; }
            throw new Error(d.detail || 'Erro na requisição.');
        }
        var ct = r.headers.get('content-type') || '';
        if (ct.indexOf('json') !== -1) return r.json();
        return r;
    }

    // ── Toast ──────────────────────────────────────────────────────
    function toast(message, type) {
        type = type || 'info';
        var container = $('#toast-container');
        var t = el('div', { className: 'toast toast-' + type }, [
            el('span', { textContent: message }),
            el('button', { className: 'toast-close', textContent: '×', onClick: function () { t.remove(); } })
        ]);
        container.appendChild(t);
        setTimeout(function () { t.remove(); }, 4500);
    }

    // ── Loading ────────────────────────────────────────────────────
    function loading(visible) {
        $('#loading-overlay').hidden = !visible;
    }

    // ── Table builder ──────────────────────────────────────────────
    function table(cols, rows) {
        var w = el('div', { className: 'table-wrapper' });
        var t = el('table', { className: 'data-table' });
        var thead = el('thead');
        var tr = el('tr');
        cols.forEach(function (c) {
            tr.appendChild(el('th', { textContent: c.label }));
        });
        thead.appendChild(tr);
        t.appendChild(thead);

        var tbody = el('tbody');
        if (!rows || !rows.length) {
            var r = el('tr');
            r.appendChild(el('td', {
                className: 'empty-row',
                colspan: String(cols.length),
                textContent: 'Nenhum registro encontrado.'
            }));
            tbody.appendChild(r);
        } else {
            rows.forEach(function (row, i) {
                var r = el('tr');
                cols.forEach(function (c) {
                    var td = el('td');
                    var v = c.render ? c.render(row[c.key], row, i) : row[c.key];
                    if (v instanceof Node) td.appendChild(v);
                    else if (c.html) td.innerHTML = v == null ? '' : v;
                    else td.textContent = v == null ? '' : v;
                    r.appendChild(td);
                });
                tbody.appendChild(r);
            });
        }
        t.appendChild(tbody);
        w.appendChild(t);
        return w;
    }

    // ── Badge ──────────────────────────────────────────────────────
    function badge(text) {
        var map = {
            'RECEBIDO':             'teal',
            'EM TRIAGEM':           'gold',
            'VENDA':                'orange',
            'S/ REPARO':            'danger',
            'ENVIADO LOJA':         'success',
            'INTERNALIZADO':        'info',
            'TRATATIVA DE SALDO':   'warning'
        };
        var cls = map[text] || 'default';
        return '<span class="badge badge-' + cls + '">' + esc(text || '—') + '</span>';
    }

    // ── Consulta module ────────────────────────────────────────────
    function renderConsulta(container) {
        var lastIds = [];

        container.innerHTML =
            '<h1 class="page-title">Consulta de Ativos</h1>' +
            '<p class="text-muted mb-3">Conversão para o padrão ServiceNow usando a base local</p>' +
            '<div class="card mb-3">' +
                '<div class="card-header">Identificadores</div>' +
                '<div class="card-body">' +
                    '<p class="text-muted">Informe ativo, etiqueta ou número de série. Aceita linha, vírgula, ponto e vírgula ou tabulação.</p>' +
                    '<textarea id="q-input" class="form-control" rows="6" ' +
                        'placeholder="Um identificador por linha"></textarea>' +
                    '<div class="btn-row mt-2">' +
                        '<button id="q-run" class="btn btn-primary">Consultar e converter</button>' +
                        '<button id="q-clear" class="btn btn-outline">Limpar</button>' +
                        '<button id="q-export" class="btn btn-secondary" disabled>Exportar SN</button>' +
                    '</div>' +
                '</div>' +
            '</div>' +
            '<div id="q-results"></div>';

        var columns = [
            { key: 'pesquisado',    label: 'Pesquisado' },
            { key: 'numero_serie',  label: 'Serial Number' },
            { key: 'modelo',        label: 'Model' },
            { key: 'etiqueta',      label: 'Asset Tag' },
            { key: 'categoria',     label: 'Model Category', html: true, render: function (v) {
                if (!v || v === 'NÃO CLASSIFICADA') {
                    return '<span style="color:#F27980">' + esc(v || 'NÃO CLASSIFICADA') + '</span>';
                }
                return esc(v);
            }},
            { key: 'stockroom',     label: 'Stockroom', render: function () { return 'SPARE - CD324'; } },
            { key: 'state',         label: 'State',     render: function () { return 'In stock'; } },
            { key: 'substate',      label: 'Substate',  render: function () { return 'Available'; } },
            { key: 'empresa',       label: 'Company' },
            { key: 'dpis',          label: 'Purchased' },
            { key: 'local_atribuido', label: 'Local Atribuído' },
            { key: 'erro',          label: 'Erro' }
        ];

        document.getElementById('q-run').onclick = async function () {
            lastIds = document.getElementById('q-input').value
                .split(/[\n,;\t]+/)
                .map(function (x) { return x.trim(); })
                .filter(Boolean);
            if (!lastIds.length) return toast('Informe identificadores.', 'warning');

            try {
                loading(true);
                var d = await api('/consulta', {
                    method: 'POST',
                    body: { identificadores: lastIds }
                });
                var out = document.getElementById('q-results');
                out.innerHTML =
                    '<p class="text-muted">' +
                    d.encontrados + ' encontrado(s) e ' +
                    d.nao_encontrados + ' não encontrado(s).</p>';
                out.appendChild(table(columns, d.resultados));
                document.getElementById('q-export').disabled = false;
                toast('Consulta concluída.', 'success');
            } catch (e) {
                toast(e.message, 'error');
            } finally {
                loading(false);
            }
        };

        document.getElementById('q-clear').onclick = function () {
            document.getElementById('q-input').value = '';
            document.getElementById('q-results').innerHTML = '';
            document.getElementById('q-export').disabled = true;
            lastIds = [];
        };

        document.getElementById('q-export').onclick = async function () {
            if (!lastIds.length) return;
            try {
                var r = await api('/consulta/export', {
                    method: 'POST',
                    body: { identificadores: lastIds }
                });
                var b = await r.blob();
                var a = document.createElement('a');
                a.href = URL.createObjectURL(b);
                a.download = 'consulta_ativos.xlsx';
                a.click();
                URL.revokeObjectURL(a.href);
            } catch (e) {
                toast(e.message, 'error');
            }
        };
    }

    // ── Init ───────────────────────────────────────────────────────
    function init() {
        renderConsulta($('#page-content'));
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
