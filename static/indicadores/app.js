/* ============================================================
   Indicadores SPARE — RMR (painel executivo)
   JS externo (CSP: script-src 'self'). Sem dependências.
   ============================================================ */
(function () {
    "use strict";

    var AUTO_MS = 120000;              // 2 min
    var SVGNS = "http://www.w3.org/2000/svg";
    var MES_ABBR = ["jan", "fev", "mar", "abr", "mai", "jun",
                    "jul", "ago", "set", "out", "nov", "dez"];
    var PAL = ["#3B82F6", "#A855F7", "#22C55E", "#F59E0B", "#EC4899", "#06B6D4"];

    var state = { auto: true, timer: null, view: "geral", dados: null };
    var tip = document.getElementById("tip");

    // ── helpers ──────────────────────────────────────────────
    function $(s, r) { return (r || document).querySelector(s); }
    function el(tag, attrs) {
        var e = document.createElementNS(SVGNS, tag);
        for (var k in (attrs || {})) e.setAttribute(k, attrs[k]);
        return e;
    }
    function fmt(n) { return (n == null ? "—" : Number(n).toLocaleString("pt-BR")); }
    function mesLabel(key) {
        if (!key || key.length < 7) return key || "";
        var y = key.slice(0, 4), m = parseInt(key.slice(5, 7), 10);
        return MES_ABBR[m - 1] + "/" + y.slice(2);
    }
    function showTip(html, ev) {
        tip.innerHTML = html;
        tip.style.opacity = "1";
        var x = ev.clientX + 14, y = ev.clientY + 14;
        var w = tip.offsetWidth, h = tip.offsetHeight;
        if (x + w > window.innerWidth - 8) x = ev.clientX - w - 14;
        if (y + h > window.innerHeight - 8) y = ev.clientY - h - 14;
        tip.style.left = x + "px"; tip.style.top = y + "px";
    }
    function hideTip() { tip.style.opacity = "0"; }

    // ── gráfico de barras verticais (série mensal) ───────────
    function barsMonthly(host, series, color) {
        host.innerHTML = "";
        if (!series || !series.length) { host.innerHTML = '<div class="empty">Sem dados.</div>'; return; }
        var W = 920, H = 300, pad = { l: 40, r: 12, t: 18, b: 34 };
        var iw = W - pad.l - pad.r, ih = H - pad.t - pad.b;
        var max = Math.max.apply(null, series.map(function (d) { return d.total; })) || 1;
        max = niceMax(max);
        var n = series.length;
        var step = iw / n;
        var bw = Math.min(54, step * 0.62);

        var svg = el("svg", { viewBox: "0 0 " + W + " " + H, width: "100%",
            height: "auto", role: "img" });

        // grid + eixo Y
        var ticks = 4;
        for (var t = 0; t <= ticks; t++) {
            var val = max * t / ticks;
            var y = pad.t + ih - (val / max) * ih;
            svg.appendChild(el("line", { x1: pad.l, y1: y, x2: W - pad.r, y2: y,
                stroke: "var(--grid)", "stroke-width": 1 }));
            var lab = el("text", { x: pad.l - 7, y: y + 4, "text-anchor": "end",
                "font-size": 11, fill: "var(--faint)" });
            lab.textContent = Math.round(val).toLocaleString("pt-BR");
            svg.appendChild(lab);
        }

        series.forEach(function (d, i) {
            var cx = pad.l + step * i + step / 2;
            var bh = (d.total / max) * ih;
            var x = cx - bw / 2, y = pad.t + ih - bh;
            var rect = el("rect", { x: x, y: y, width: bw, height: bh, rx: 5,
                fill: color, class: "bar-rect" });
            rect.style.cursor = "pointer";
            rect.addEventListener("mousemove", function (ev) {
                showTip('<div class="t">' + mesFull(d.mes) + '</div><b>' + fmt(d.total) + "</b> chamado(s)", ev);
            });
            rect.addEventListener("mouseleave", hideTip);
            svg.appendChild(rect);

            if (d.total > 0) {
                var vlab = el("text", { x: cx, y: y - 6, "text-anchor": "middle",
                    "font-size": 11.5, "font-weight": 700, fill: "var(--ink2)" });
                vlab.textContent = fmt(d.total);
                svg.appendChild(vlab);
            }
            var xl = el("text", { x: cx, y: H - 12, "text-anchor": "middle",
                "font-size": 11, fill: "var(--muted)" });
            xl.textContent = mesLabel(d.mes);
            svg.appendChild(xl);
        });
        host.appendChild(svg);
    }

    // ── gráfico de linha/área (SLA %) ────────────────────────
    function lineMonthly(host, series) {
        host.innerHTML = "";
        if (!series || !series.length) { host.innerHTML = '<div class="empty">Sem dados.</div>'; return; }
        var W = 460, H = 300, pad = { l: 34, r: 12, t: 18, b: 34 };
        var iw = W - pad.l - pad.r, ih = H - pad.t - pad.b;
        var n = series.length;
        var step = n > 1 ? iw / (n - 1) : 0;
        function X(i) { return pad.l + step * i; }
        function Y(v) { return pad.t + ih - (Math.max(0, Math.min(100, v)) / 100) * ih; }

        var svg = el("svg", { viewBox: "0 0 " + W + " " + H, width: "100%", height: "auto" });
        [0, 25, 50, 75, 100].forEach(function (v) {
            var y = Y(v);
            svg.appendChild(el("line", { x1: pad.l, y1: y, x2: W - pad.r, y2: y,
                stroke: "var(--grid)", "stroke-width": 1 }));
            var lab = el("text", { x: pad.l - 6, y: y + 4, "text-anchor": "end",
                "font-size": 10, fill: "var(--faint)" });
            lab.textContent = v;
            svg.appendChild(lab);
        });

        var pts = series.map(function (d, i) { return [X(i), Y(d.pct)]; });
        // área
        if (n > 1) {
            var dArea = "M" + pts[0][0] + "," + Y(0);
            pts.forEach(function (p) { dArea += " L" + p[0] + "," + p[1]; });
            dArea += " L" + pts[n - 1][0] + "," + Y(0) + " Z";
            svg.appendChild(el("path", { d: dArea, fill: "rgba(34,197,94,.14)" }));
            var dLine = "M" + pts.map(function (p) { return p[0] + "," + p[1]; }).join(" L");
            svg.appendChild(el("path", { d: dLine, fill: "none", stroke: "var(--good)", "stroke-width": 2.5,
                "stroke-linejoin": "round", "stroke-linecap": "round" }));
        }
        series.forEach(function (d, i) {
            var c = el("circle", { cx: X(i), cy: Y(d.pct), r: 4.5, fill: "#0A0F1A",
                stroke: "var(--good)", "stroke-width": 2.5 });
            c.style.cursor = "pointer";
            c.addEventListener("mousemove", function (ev) {
                showTip('<div class="t">' + mesFull(d.mes) + '</div><b>' + d.pct +
                    "%</b> no prazo<br>" + fmt(d.dentro) + "/" + fmt(d.total) + " ANS", ev);
            });
            c.addEventListener("mouseleave", hideTip);
            svg.appendChild(c);
            var xl = el("text", { x: X(i), y: H - 12, "text-anchor": "middle",
                "font-size": 9.5, fill: "var(--muted)" });
            xl.textContent = mesLabel(d.mes);
            svg.appendChild(xl);
        });
        host.appendChild(svg);
    }

    // ── ranking horizontal (DOM) ─────────────────────────────
    function ranking(host, items, color, unidade) {
        host.innerHTML = "";
        if (!items || !items.length) { host.innerHTML = '<div class="empty">Sem dados.</div>'; return; }
        var max = Math.max.apply(null, items.map(function (d) { return d.total; })) || 1;
        items.forEach(function (d, i) {
            var row = document.createElement("div"); row.className = "rrow";
            var nm = document.createElement("div"); nm.className = "nm";
            nm.textContent = d.nome; nm.title = d.nome;
            var track = document.createElement("div"); track.className = "track";
            var fill = document.createElement("div"); fill.className = "fill";
            fill.style.background = (typeof color === "function") ? color(i) : color;
            fill.style.width = "3px";
            track.appendChild(fill);
            var qt = document.createElement("div"); qt.className = "qt";
            qt.textContent = fmt(d.total);
            row.appendChild(nm); row.appendChild(track); row.appendChild(qt);
            row.addEventListener("mousemove", function (ev) {
                showTip('<div class="t">' + escapeHtml(d.nome) + '</div><b>' + fmt(d.total) +
                    "</b> " + (unidade || "chamado(s)"), ev);
            });
            row.addEventListener("mouseleave", hideTip);
            host.appendChild(row);
            requestAnimationFrame(function () {
                fill.style.width = Math.max(3, (d.total / max) * 100) + "%";
            });
        });
    }

    function niceMax(v) {
        if (v <= 5) return 5;
        var pow = Math.pow(10, Math.floor(Math.log10(v)));
        var f = v / pow;
        var nf = f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10;
        return nf * pow;
    }
    function mesFull(key) {
        if (!key || key.length < 7) return key || "";
        var nomes = ["Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho",
            "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro"];
        return nomes[parseInt(key.slice(5, 7), 10) - 1] + " " + key.slice(0, 4);
    }
    function escapeHtml(s) {
        return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) {
            return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
        });
    }

    // ── render ───────────────────────────────────────────────
    function renderKpis(k) {
        var host = $("#kpis");
        host.innerHTML = "";
        var cards = [
            { lbl: "Backlog", val: k ? k.backlog : null, cor: "var(--accent)", hint: "Abertos (Novo · Em and. · Em espera)" },
            { lbl: "RITMs", val: k ? k.ritms : null, cor: "var(--c1)", hint: "Solicitações abertas (não fechadas)" },
            { lbl: "AG. Atendimento", val: k ? k.ag_atendimento : null, cor: "var(--c4)", hint: "Novo + Em andamento" },
            { lbl: "Priorizados", val: (k && k.priorizados != null) ? k.priorizados : null,
              cor: "var(--bad)", hint: (k && k.priorizados != null) ? "Marcados para priorização" : "Configurar SN_PRIORITIZED_QUERY" }
        ];
        cards.forEach(function (c) {
            var d = document.createElement("div"); d.className = "kpi";
            d.style.setProperty("--k", c.cor);
            d.innerHTML = '<div class="lbl">' + c.lbl + '</div>' +
                '<div class="val">' + (c.val == null ? "—" : fmt(c.val)) + '</div>' +
                '<div class="hint">' + c.hint + '</div>';
            host.appendChild(d);
        });
    }

    function renderAll(snap) {
        var d = (snap && snap.dados) || {};
        // Visão Geral
        renderKpis(d.kpis);
        barsMonthly($("#ch-tratado"), d.tratado_por_mes, "var(--accent)");
        var sla = d.sla || {};
        lineMonthly($("#ch-sla"), sla.por_mes);
        $("#sla-big").textContent = (sla.compliance_pct != null ? sla.compliance_pct + "%" : "—");
        barsMonthly($("#ch-backlog"), d.backlog_por_mes, "var(--c1)");
        ranking($("#ch-status"), d.abertos_por_status, "var(--c4)");
        ranking($("#ch-local"), d.por_localidade, "var(--c2)");
        ranking($("#ch-bu"), d.por_bu, function (i) { return PAL[i % PAL.length]; });
        // SLED & Coletores
        barsMonthly($("#ch-sled"), d.sled_por_mes, "var(--c2)");
        barsMonthly($("#ch-coletor"), d.coletor_por_mes, "var(--c6)");
        ranking($("#ch-subs"), d.por_subcategoria, "var(--c5)");
        var sledT = sumSerie(d.sled_por_mes), colT = sumSerie(d.coletor_por_mes);
        $("#sled-big").textContent = sledT != null ? fmt(sledT) : "—";
        $("#col-big").textContent = colT != null ? fmt(colT) : "—";
    }
    function sumSerie(s) {
        if (!s || !s.length) return null;
        return s.reduce(function (a, x) { return a + (x.total || 0); }, 0);
    }

    // ── status bar ───────────────────────────────────────────
    function setStatus(snap, erroMsg) {
        var sb = $("#statusbar");
        if (erroMsg) { sb.innerHTML = '<span class="err">' + escapeHtml(erroMsg) + "</span>"; return; }
        var parts = [];
        if (snap) {
            parts.push('<span class="chip">Referência: <b>&nbsp;' + escapeHtml(snap.referencia || "—") + "</b></span>");
            if (snap.criado_em) {
                var dt = new Date(snap.criado_em);
                parts.push('<span class="chip">Atualizado: &nbsp;' + dt.toLocaleString("pt-BR") + "</span>");
            }
            var erros = (snap.dados && snap.dados.erros) || {};
            var ne = Object.keys(erros).length;
            if (ne) parts.push('<span class="err">' + ne + " indicador(es) com erro no ServiceNow</span>");
        } else {
            parts.push("Nenhum snapshot ainda — clique em Atualizar.");
        }
        sb.innerHTML = parts.join(" ");
    }

    // ── carregar / atualizar ─────────────────────────────────
    function carregar() {
        return fetch("/api/indicadores/dados", { credentials: "same-origin" })
            .then(function (r) { return r.json(); })
            .then(function (j) {
                state.dados = j.snapshot;
                setStatus(j.snapshot, null);
                renderAll(j.snapshot);
            })
            .catch(function (e) { setStatus(null, "Falha ao carregar: " + e.message); });
    }

    function atualizar() {
        var btn = $("#btn-refresh");
        btn.disabled = true; var txt = btn.textContent; btn.textContent = "⏳ Atualizando…";
        fetch("/api/indicadores/atualizar", { method: "POST", credentials: "same-origin" })
            .then(function (r) {
                if (!r.ok) return r.json().then(function (j) { throw new Error(j.detail || ("HTTP " + r.status)); });
                return r.json();
            })
            .then(function (j) {
                state.dados = { referencia: j.referencia, criado_em: new Date().toISOString(), dados: j.dados };
                setStatus(state.dados, null);
                renderAll(state.dados);
            })
            .catch(function (e) { setStatus(null, "Falha ao atualizar: " + e.message); })
            .finally(function () { btn.disabled = false; btn.textContent = txt; });
    }

    // ── auto refresh ─────────────────────────────────────────
    function startAuto() {
        if (state.timer) clearInterval(state.timer);
        state.timer = setInterval(function () { if (state.auto) carregar(); }, AUTO_MS);
    }
    function toggleAuto() {
        state.auto = !state.auto;
        var pill = $("#live-pill"), btn = $("#btn-auto");
        if (state.auto) {
            pill.classList.remove("paused"); $("#live-txt").textContent = "Ao vivo · 2 min";
            btn.textContent = "⏸ Pausar"; carregar();
        } else {
            pill.classList.add("paused"); $("#live-txt").textContent = "Pausado";
            btn.textContent = "▶ Retomar";
        }
    }

    // ── views ────────────────────────────────────────────────
    function setView(v) {
        state.view = v;
        Array.prototype.forEach.call(document.querySelectorAll("#tabs button"), function (b) {
            b.classList.toggle("active", b.getAttribute("data-view") === v);
        });
        Array.prototype.forEach.call(document.querySelectorAll(".view"), function (s) {
            s.classList.toggle("active", s.getAttribute("data-view") === v);
        });
        // re-render para animar as barras da view que apareceu
        if (state.dados) renderAll(state.dados);
    }

    // ── init ─────────────────────────────────────────────────
    document.addEventListener("DOMContentLoaded", function () {
        $("#btn-refresh").addEventListener("click", atualizar);
        $("#btn-auto").addEventListener("click", toggleAuto);
        Array.prototype.forEach.call(document.querySelectorAll("#tabs button"), function (b) {
            b.addEventListener("click", function () { setView(b.getAttribute("data-view")); });
        });
        window.addEventListener("resize", function () { hideTip(); });
        carregar();
        startAuto();
    });
})();
