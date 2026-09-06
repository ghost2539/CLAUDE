/* EBS Forms (RPA) — tela de operação e administração.
 *
 * Toda chamada vai para /api/ebs-forms/*. A rodada do robô é assíncrona:
 * o servidor devolve o id da execução e a tela acompanha por polling
 * (GET /execucoes/{id}) até a situação sair de "rodando".
 */
(function () {
    "use strict";

    var API = "/api/ebs-forms";
    var MODULO = "ebs_forms";
    var INTERVALO_POLL = 3000;

    var estado = {
        admin: false,
        podeCriar: false,
        acoes: [],
        variaveis: [],
        execucaoAtiva: null,   // id da execução aberta no bloco 3
        pollConsulta: null,    // timer do bloco 2
        pollExec: null,        // timer do bloco 3
    };

    // ── utilidades ─────────────────────────────────────────────────────
    function $(id) { return document.getElementById(id); }

    function el(tag, attrs, filhos) {
        var e = document.createElement(tag);
        if (attrs) {
            Object.keys(attrs).forEach(function (k) {
                if (k === "class") e.className = attrs[k];
                else if (k === "text") e.textContent = attrs[k];
                else if (k === "html") e.innerHTML = attrs[k];
                else if (k.indexOf("on") === 0) e.addEventListener(k.slice(2), attrs[k]);
                else if (attrs[k] === true) e.setAttribute(k, "");
                else if (attrs[k] !== false && attrs[k] != null) e.setAttribute(k, attrs[k]);
            });
        }
        (filhos || []).forEach(function (f) {
            if (f == null) return;
            e.appendChild(typeof f === "string" ? document.createTextNode(f) : f);
        });
        return e;
    }

    function limpar(node) { while (node.firstChild) node.removeChild(node.firstChild); }

    function api(metodo, caminho, corpo) {
        var opt = { method: metodo, credentials: "same-origin", headers: {} };
        if (corpo !== undefined) {
            opt.headers["Content-Type"] = "application/json";
            opt.body = JSON.stringify(corpo);
        }
        return fetch(API + caminho, opt).then(function (r) {
            if (r.status === 401) {
                window.location.href = "/";
                throw new Error("Sessão expirada.");
            }
            return r.text().then(function (txt) {
                var dados = null;
                try { dados = txt ? JSON.parse(txt) : null; } catch (e) { dados = null; }
                if (!r.ok) {
                    var msg = (dados && (dados.detail || dados.erro || dados.message)) || txt || ("HTTP " + r.status);
                    if (typeof msg !== "string") msg = JSON.stringify(msg);
                    throw new Error(msg);
                }
                return dados;
            });
        });
    }

    function fmtData(iso) {
        if (!iso) return "—";
        var d = new Date(iso);
        if (isNaN(d.getTime())) return iso;
        // Datas vêm em UTC sem sufixo; tratamos como UTC para não deslocar.
        if (!/Z|[+-]\d\d:?\d\d$/.test(iso)) d = new Date(iso + "Z");
        return d.toLocaleString("pt-BR");
    }

    function duracao(ini, fim) {
        if (!ini || !fim) return "";
        var a = new Date(/Z|[+-]\d\d:?\d\d$/.test(ini) ? ini : ini + "Z");
        var b = new Date(/Z|[+-]\d\d:?\d\d$/.test(fim) ? fim : fim + "Z");
        var s = Math.round((b - a) / 1000);
        if (isNaN(s) || s < 0) return "";
        return s < 60 ? s + " s" : Math.floor(s / 60) + " min " + (s % 60) + " s";
    }

    function classeSituacao(sit) {
        if (sit === "rodando") return "rodando";
        if (sit === "ok" || sit === "sucesso" || sit === "concluida" || sit === "concluída") return "ok";
        if (sit === "erro" || sit === "falha") return "erro";
        return "neutro";
    }

    function selo(sit) {
        return el("span", { class: "selo " + classeSituacao(sit), text: sit || "—" });
    }

    function avisar(msg, tipo) {
        var a = $("aviso");
        if (!msg) { a.hidden = true; return; }
        a.textContent = msg;
        a.className = "aviso" + (tipo ? " " + tipo : "");
        a.hidden = false;
        window.clearTimeout(avisar._t);
        avisar._t = window.setTimeout(function () { a.hidden = true; }, 8000);
    }

    // ── sessão / permissões ────────────────────────────────────────────
    function carregarSessao() {
        return fetch("/api/auth/me", { credentials: "same-origin" })
            .then(function (r) { if (!r.ok) throw new Error("sem sessão"); return r.json(); })
            .then(function (me) {
                var mapa = (me.permission_map || {})[MODULO] || {};
                estado.admin = !!(me.is_admin || mapa.can_admin);
                estado.podeCriar = !!(me.is_admin || mapa.can_create);
                $("quem").textContent = (me.display_name || me.username || "") + (estado.admin ? " · admin" : "");
                document.querySelectorAll(".admin").forEach(function (n) { n.hidden = !estado.admin; });
                if (!estado.podeCriar) {
                    $("bt-consultar").disabled = true;
                    $("bt-consultar").title = "Seu usuário não tem permissão para disparar consultas.";
                }
            })
            .catch(function () { window.location.href = "/"; });
    }

    // ── 1. status ──────────────────────────────────────────────────────
    function itemStatus(rotulo, valor, situacao) {
        return el("div", { class: "item-status " + (situacao || "neutro") }, [
            el("div", { class: "rot", text: rotulo }),
            el("div", { class: "val", text: valor }),
        ]);
    }

    function carregarStatus() {
        var g = $("status-grade");
        return api("GET", "/status").then(function (d) {
            limpar(g);
            var simNao = function (v) { return v ? "sim" : "não"; };
            var okRuim = function (v) { return v ? "ok" : "ruim"; };
            var credOk = /^ok/.test(d.credenciais || "");
            g.appendChild(itemStatus("Java", d.java ? (d.java_versao || d.java) : "não encontrado", okRuim(d.java)));
            g.appendChild(itemStatus("javac", d.javac || "não encontrado", d.javac ? "ok" : "neutro"));
            g.appendChild(itemStatus("Lançador compilado", simNao(d.lancador_compilado), okRuim(d.lancador_compilado)));
            g.appendChild(itemStatus("Xvfb", d.xvfb || "não encontrado", okRuim(d.xvfb)));
            g.appendChild(itemStatus("Display " + (d.display || ""), d.display_ativo ? "ativo" : "inativo", d.display_ativo ? "ok" : "neutro"));
            g.appendChild(itemStatus("Credenciais do robô", d.credenciais || "—", okRuim(credOk)));
            g.appendChild(itemStatus("URL da função definida", simNao(d.funcao_url_definida), d.funcao_url_definida ? "ok" : "neutro"));
            g.appendChild(itemStatus("Função / responsabilidade", (d.funcao || "—") + " / " + (d.responsabilidade || "—"), "neutro"));
            g.appendChild(itemStatus("Livros", (d.livros || []).join(", ") || "—", "neutro"));
            g.appendChild(itemStatus("Sessão Forms", d.ocupado ? "em andamento" : "livre", d.ocupado ? "rodando" : "ok"));
            g.appendChild(itemStatus("Execuções / ativos coletados", (d.execucoes || 0) + " / " + (d.ativos || 0), "neutro"));
            g.appendChild(itemStatus("Servidor", d.servidor || "—", "neutro"));
            g.appendChild(itemStatus("Pasta de dados", d.pasta || "—", "neutro"));
            if (estado.admin) $("bt-compilar").disabled = !d.javac;
        }).catch(function (e) {
            limpar(g);
            g.appendChild(el("p", { class: "mudo", text: "Não foi possível ler o status: " + e.message }));
        });
    }

    function compilar() {
        var bt = $("bt-compilar"), saida = $("compilar-saida");
        bt.disabled = true;
        saida.hidden = false;
        saida.textContent = "Compilando…";
        api("POST", "/compilar").then(function (d) {
            saida.textContent = (d.saida || "(sem saída)") + "\n\ncompilado: " + (d.compilado ? "sim" : "não");
            avisar("Lançador compilado.", "ok");
            carregarStatus();
        }).catch(function (e) {
            saida.textContent = "Falha ao compilar: " + e.message;
            avisar("Falha ao compilar: " + e.message, "erro");
        }).then(function () { bt.disabled = false; });
    }

    function testarAbertura() {
        var bt = $("bt-testar");
        bt.disabled = true;
        api("POST", "/testar-abertura").then(function (d) {
            avisar("Teste de abertura iniciado (execução #" + d.execucao_id + ").", "ok");
            carregarExecucoes();
            abrirExecucao(d.execucao_id);
        }).catch(function (e) {
            avisar("Não foi possível iniciar o teste: " + e.message, "erro");
        }).then(function () { bt.disabled = false; });
    }

    // ── blocos de detalhe (log + resultado + capturas) ─────────────────
    function tabelaDados(obj) {
        var t = el("table", { class: "dados" });
        Object.keys(obj || {}).forEach(function (k) {
            var v = obj[k], celula;
            if (v !== null && typeof v === "object") {
                celula = el("td", {}, [el("pre", { text: JSON.stringify(v, null, 2) })]);
            } else {
                celula = el("td", { text: (v === "" || v == null) ? "—" : String(v) });
            }
            t.appendChild(el("tr", {}, [el("th", { text: k }), celula]));
        });
        if (!t.firstChild) t.appendChild(el("tr", {}, [el("td", { class: "mudo", text: "Nenhum dado." })]));
        return t;
    }

    function ampliar(src) {
        var lupa = el("div", { class: "lupa", onclick: function () { document.body.removeChild(lupa); } },
            [el("img", { src: src, alt: "captura ampliada" })]);
        document.body.appendChild(lupa);
    }

    function grelhaCapturas(nomes) {
        var g = el("div", { class: "capturas" });
        (nomes || []).forEach(function (n) {
            var nome = String(n).split("/").pop();
            var src = API + "/capturas/" + encodeURIComponent(nome);
            g.appendChild(el("div", { class: "captura" }, [
                el("img", { src: src, alt: nome, loading: "lazy", onclick: function () { ampliar(src); } }),
                el("div", { class: "leg", text: nome }),
            ]));
        });
        if (!g.firstChild) g.appendChild(el("p", { class: "mudo", text: "Nenhuma captura." }));
        return g;
    }

    function montarDetalhe(destino, ex) {
        limpar(destino);
        destino.hidden = false;
        var titulo = "Execução #" + ex.id + " · " + (ex.tipo || "") +
            ((ex.parametros && ex.parametros.criterio) ? " · " + ex.parametros.criterio : "") +
            (ex.usuario ? " · " + ex.usuario : "");
        destino.appendChild(el("div", { class: "detalhe-cab" }, [
            el("h3", { text: titulo }),
            el("div", {}, [selo(ex.situacao), " ",
                el("span", { class: "mudo", text: fmtData(ex.inicio) + (ex.fim ? " → " + fmtData(ex.fim) + " (" + duracao(ex.inicio, ex.fim) + ")" : "") })]),
        ]));
        if (ex.erro) destino.appendChild(el("div", { class: "aviso erro", text: ex.erro }));

        var res = ex.resultado || {};
        if (res.dados && Object.keys(res.dados).length) {
            destino.appendChild(el("div", {}, [
                el("h3", { text: "Ativo" + (res.livro ? " (livro " + res.livro + ")" : "") + (res.encontrado === false ? " — não encontrado" : "") }),
                tabelaDados(res.dados),
            ]));
        } else if (res.encontrado === false) {
            destino.appendChild(el("p", { class: "mudo", text: "Ativo não encontrado em nenhum livro." }));
        } else if (ex.tipo === "teste_abertura" && (res.janelas || res.resultado)) {
            destino.appendChild(el("div", {}, [el("h3", { text: "Resultado" }), tabelaDados({ janelas: res.janelas, resultado: res.resultado })]));
        }

        destino.appendChild(el("div", {}, [el("h3", { text: "Capturas" }), grelhaCapturas(ex.capturas)]));
        var log = el("pre", { class: "log", text: ex.log || "(sem log ainda)" });
        destino.appendChild(el("div", {}, [el("h3", { text: "Log" }), log]));
        log.scrollTop = log.scrollHeight;
    }

    // Consulta a execução até sair de "rodando"; chama `aoAtualizar` a cada volta.
    function acompanhar(id, aoAtualizar, chaveTimer) {
        window.clearTimeout(estado[chaveTimer]);
        function volta() {
            api("GET", "/execucoes/" + id).then(function (ex) {
                aoAtualizar(ex);
                if (ex.situacao === "rodando") {
                    estado[chaveTimer] = window.setTimeout(volta, INTERVALO_POLL);
                } else {
                    carregarExecucoes();
                    carregarStatus();
                }
            }).catch(function (e) {
                avisar("Falha ao acompanhar a execução #" + id + ": " + e.message, "erro");
            });
        }
        volta();
    }

    // ── 2. consultar ───────────────────────────────────────────────────
    function consultar(ev) {
        ev.preventDefault();
        var criterio = $("criterio").value.trim();
        if (!criterio) return;
        var bt = $("bt-consultar"), est = $("consulta-estado"), det = $("consulta-detalhe");
        bt.disabled = true;
        det.hidden = true;
        limpar(det);
        est.hidden = false;
        est.className = "estado rodando";
        est.textContent = "Enviando consulta…";
        api("POST", "/consultar", { criterio: criterio, forcar: $("forcar").checked }).then(function (d) {
            if (d.cache) {
                est.className = "estado ok";
                est.textContent = "Dado já coletado em " + fmtData(d.coletado_em) +
                    (d.execucao_id ? " (execução #" + d.execucao_id + ")" : "") +
                    ". Marque \"Ignorar cache\" para consultar de novo no Forms.";
                det.hidden = false;
                det.appendChild(el("div", {}, [
                    el("h3", { text: "Ativo " + d.criterio + (d.livro ? " (livro " + d.livro + ")" : "") }),
                    tabelaDados(d.dados),
                ]));
                if (d.execucao_id) {
                    det.appendChild(el("button", { type: "button", class: "bt bt-sec", onclick: function () { abrirExecucao(d.execucao_id); } },
                        ["Ver execução #" + d.execucao_id]));
                }
                bt.disabled = false;
                return;
            }
            est.textContent = "Execução #" + d.execucao_id + " em andamento — abrir o Forms leva de 20 a 60 s…";
            carregarExecucoes();
            acompanhar(d.execucao_id, function (ex) {
                if (ex.situacao === "rodando") {
                    est.className = "estado rodando";
                    est.textContent = "Execução #" + ex.id + " em andamento (" + duracao(ex.inicio, new Date().toISOString()) + ")…";
                } else {
                    est.className = "estado " + classeSituacao(ex.situacao);
                    est.textContent = "Execução #" + ex.id + " terminou: " + ex.situacao + (ex.erro ? " — " + ex.erro : "");
                    bt.disabled = !estado.podeCriar;
                }
                montarDetalhe(det, ex);
            }, "pollConsulta");
        }).catch(function (e) {
            est.className = "estado erro";
            est.textContent = "Falha: " + e.message;
            bt.disabled = !estado.podeCriar;
        });
    }

    // ── 3. execuções ───────────────────────────────────────────────────
    function carregarExecucoes() {
        var tb = $("tab-execucoes").querySelector("tbody");
        return api("GET", "/execucoes?limite=50").then(function (lista) {
            limpar(tb);
            if (!lista.length) {
                tb.appendChild(el("tr", {}, [el("td", { colspan: 8, class: "mudo", text: "Nenhuma execução ainda." })]));
                return;
            }
            lista.forEach(function (ex) {
                var crit = (ex.parametros && ex.parametros.criterio) || "—";
                var tr = el("tr", { class: "clicavel" + (ex.id === estado.execucaoAtiva ? " ativa" : ""), onclick: function () { abrirExecucao(ex.id); } }, [
                    el("td", { text: "#" + ex.id }),
                    el("td", { text: ex.tipo || "" }),
                    el("td", { text: crit }),
                    el("td", { text: ex.usuario || "" }),
                    el("td", {}, [selo(ex.situacao)]),
                    el("td", { text: fmtData(ex.inicio) }),
                    el("td", { text: ex.fim ? fmtData(ex.fim) + " (" + duracao(ex.inicio, ex.fim) + ")" : "—" }),
                    el("td", { text: String((ex.capturas || []).length) }),
                ]);
                tb.appendChild(tr);
            });
        }).catch(function (e) {
            limpar(tb);
            tb.appendChild(el("tr", {}, [el("td", { colspan: 8, class: "mudo", text: "Falha ao listar: " + e.message })]));
        });
    }

    function abrirExecucao(id) {
        estado.execucaoAtiva = id;
        var det = $("exec-detalhe");
        det.hidden = false;
        limpar(det);
        det.appendChild(el("p", { class: "mudo", text: "Carregando execução #" + id + "…" }));
        document.querySelectorAll("#tab-execucoes tbody tr").forEach(function (tr) {
            tr.classList.toggle("ativa", tr.firstChild && tr.firstChild.textContent === "#" + id);
        });
        acompanhar(id, function (ex) { montarDetalhe(det, ex); }, "pollExec");
        det.scrollIntoView({ behavior: "smooth", block: "start" });
    }

    // ── 4. roteiros (admin) ────────────────────────────────────────────
    function carregarRoteiros() {
        if (!estado.admin) return Promise.resolve();
        var lista = $("roteiros-lista");
        return api("GET", "/roteiros").then(function (d) {
            estado.acoes = d.acoes || [];
            estado.variaveis = d.variaveis || [];
            var ajuda = $("roteiros-ajuda");
            limpar(ajuda);
            ajuda.appendChild(document.createTextNode("Ações: "));
            estado.acoes.forEach(function (a, i) {
                if (i) ajuda.appendChild(document.createTextNode(", "));
                ajuda.appendChild(el("code", { text: a, title: descricaoAcao(a) }));
            });
            ajuda.appendChild(document.createTextNode(" · Variáveis no argumento: "));
            estado.variaveis.forEach(function (v, i) {
                if (i) ajuda.appendChild(document.createTextNode(", "));
                ajuda.appendChild(el("code", { text: v }));
            });
            ajuda.appendChild(document.createTextNode(". Passe o mouse sobre a ação para ver o que ela faz."));
            limpar(lista);
            (d.roteiros || []).forEach(function (r) { lista.appendChild(montarRoteiro(r)); });
            if (!lista.firstChild) lista.appendChild(el("p", { class: "mudo", text: "Nenhum roteiro cadastrado." }));
        }).catch(function (e) {
            limpar(lista);
            lista.appendChild(el("p", { class: "mudo", text: "Falha ao carregar roteiros: " + e.message }));
        });
    }

    function descricaoAcao(a) {
        return {
            esperar: "Aguarda o argumento em milissegundos (padrão 1000).",
            tecla: "Envia uma tecla ou combinação (ex.: TAB, ENTER, ALT+L).",
            texto: "Cola o argumento inteiro no campo com foco.",
            digitar: "Digita o argumento tecla a tecla.",
            copiar: "Copia o campo com foco e guarda no resultado sob o nome do passo.",
            foto: "Captura a tela com o nome do passo.",
            se_vazio: "Pula os passos até fim_se se o valor copiado no nome indicado no argumento NÃO estiver vazio.",
            fim_se: "Fecha o bloco iniciado por se_vazio.",
            documento: "Baixa o próximo documento que o Forms abriu e guarda o texto sob o nome do passo.",
        }[a] || "";
    }

    function linhaPasso(passo) {
        var sel = el("select", { class: "acao" });
        var acoes = estado.acoes.slice();
        if (passo.acao && acoes.indexOf(passo.acao) < 0) acoes.push(passo.acao);
        acoes.forEach(function (a) {
            sel.appendChild(el("option", { value: a, text: a, title: descricaoAcao(a) }));
        });
        sel.value = passo.acao || acoes[0] || "";
        var tr = el("tr", {}, [
            el("td", { class: "n" }),
            el("td", {}, [sel]),
            el("td", {}, [el("input", { class: "arg", type: "text", value: passo.arg == null ? "" : String(passo.arg), placeholder: "argumento" })]),
            el("td", {}, [el("input", { class: "nome", type: "text", value: passo.nome || "", placeholder: "nome" })]),
            el("td", { class: "ops" }, [
                el("button", { type: "button", class: "bt bt-sec bt-mini", title: "Subir", onclick: function () { mover(tr, -1); } }, ["▲"]),
                el("button", { type: "button", class: "bt bt-sec bt-mini", title: "Descer", onclick: function () { mover(tr, 1); } }, ["▼"]),
                el("button", { type: "button", class: "bt bt-sec bt-mini", title: "Inserir abaixo", onclick: function () {
                    tr.parentNode.insertBefore(linhaPasso({}), tr.nextSibling); renumerar(tr.parentNode);
                } }, ["+"]),
                el("button", { type: "button", class: "bt bt-perigo bt-mini", title: "Remover", onclick: function () {
                    var tb = tr.parentNode; tb.removeChild(tr); renumerar(tb);
                } }, ["×"]),
            ]),
        ]);
        return tr;
    }

    function mover(tr, delta) {
        var tb = tr.parentNode;
        if (delta < 0 && tr.previousElementSibling) tb.insertBefore(tr, tr.previousElementSibling);
        else if (delta > 0 && tr.nextElementSibling) tb.insertBefore(tr.nextElementSibling, tr);
        renumerar(tb);
    }

    function renumerar(tb) {
        Array.prototype.forEach.call(tb.querySelectorAll("tr"), function (tr, i) {
            tr.querySelector("td.n").textContent = String(i + 1);
        });
    }

    function lerPassos(tb) {
        return Array.prototype.map.call(tb.querySelectorAll("tr"), function (tr) {
            var p = { acao: tr.querySelector(".acao").value };
            var arg = tr.querySelector(".arg").value;
            var nome = tr.querySelector(".nome").value.trim();
            if (arg !== "") p.arg = arg;
            if (nome) p.nome = nome;
            return p;
        });
    }

    function montarRoteiro(r) {
        var tb = el("tbody");
        (r.passos || []).forEach(function (p) { tb.appendChild(linhaPasso(p)); });
        renumerar(tb);
        var desc = el("input", { class: "roteiro-desc", type: "text", value: r.descricao || "", placeholder: "Descrição do roteiro" });
        var ativo = el("input", { type: "checkbox" });
        ativo.checked = r.ativo !== false;
        var msg = el("span", { class: "roteiro-msg" });
        var btSalvar = el("button", { type: "button", class: "bt", onclick: salvar }, ["Salvar"]);
        var btRestaurar = el("button", { type: "button", class: "bt bt-perigo", onclick: restaurar }, ["Restaurar padrão"]);

        function aviso(t, cls) { msg.textContent = t; msg.className = "roteiro-msg" + (cls ? " " + cls : ""); }

        function salvar() {
            var passos = lerPassos(tb);
            if (!passos.length) { aviso("O roteiro precisa de ao menos um passo.", "erro"); return; }
            btSalvar.disabled = true;
            aviso("Salvando…");
            api("PUT", "/roteiros/" + encodeURIComponent(r.nome), { descricao: desc.value.trim(), passos: passos, ativo: ativo.checked })
                .then(function () { aviso("Salvo.", "ok"); return carregarRoteiros(); })
                .catch(function (e) { aviso("Falha ao salvar: " + e.message, "erro"); btSalvar.disabled = false; });
        }

        function restaurar() {
            if (!window.confirm("Descartar as alterações do roteiro \"" + r.nome + "\" e voltar ao padrão?")) return;
            btRestaurar.disabled = true;
            api("POST", "/roteiros/" + encodeURIComponent(r.nome) + "/restaurar")
                .then(function () { avisar("Roteiro \"" + r.nome + "\" restaurado.", "ok"); return carregarRoteiros(); })
                .catch(function (e) { aviso("Falha ao restaurar: " + e.message, "erro"); btRestaurar.disabled = false; });
        }

        return el("div", { class: "roteiro" }, [
            el("div", { class: "roteiro-cab" }, [
                el("h3", { text: r.nome }),
                el("span", { class: "meta", text: r.atualizado_em ? "atualizado em " + fmtData(r.atualizado_em) + (r.atualizado_por ? " por " + r.atualizado_por : "") : "" }),
                el("div", { class: "acoes" }, [
                    el("button", { type: "button", class: "bt bt-sec bt-mini", onclick: function () { tb.appendChild(linhaPasso({})); renumerar(tb); } }, ["+ passo"]),
                ]),
            ]),
            desc,
            el("div", { class: "tabela-rolagem" }, [
                el("table", { class: "passos" }, [
                    el("thead", {}, [el("tr", {}, [
                        el("th", { text: "#" }), el("th", { text: "Ação" }), el("th", { text: "Argumento" }),
                        el("th", { text: "Nome" }), el("th", { text: "" })])]),
                    tb,
                ]),
            ]),
            el("div", { class: "roteiro-rodape" }, [
                el("label", { class: "chk" }, [ativo, " Ativo"]),
                msg,
                el("div", { class: "direita" }, [btRestaurar, btSalvar]),
            ]),
        ]);
    }

    // ── arranque ───────────────────────────────────────────────────────
    function iniciar() {
        $("bt-status").addEventListener("click", carregarStatus);
        $("bt-compilar").addEventListener("click", compilar);
        $("bt-testar").addEventListener("click", testarAbertura);
        $("form-consulta").addEventListener("submit", consultar);
        $("bt-execucoes").addEventListener("click", carregarExecucoes);
        $("bt-roteiros").addEventListener("click", carregarRoteiros);
        carregarSessao().then(function () {
            carregarStatus();
            carregarExecucoes();
            carregarRoteiros();
        });
    }

    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", iniciar);
    else iniciar();
})();
