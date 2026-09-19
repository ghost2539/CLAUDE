/* Tela de login — o ÚNICO JavaScript que sai sem sessão.
 *
 * Existe separado do app.js por um motivo de segurança, não de organização:
 * enquanto os dois eram o mesmo arquivo em /static, qualquer visitante
 * baixava 27 KB com as rotas dos módulos e os nomes das permissões, e o
 * index.html vinha junto com os 35 itens de menu — visíveis em "ver
 * código-fonte", sem devtools. O portal agora mora noutra página, servida
 * só a quem tem sessão, e o app.js saiu de /static para uma rota que
 * pergunta quem é antes de responder.
 *
 * Por isso este arquivo é pequeno de propósito: tudo que estiver aqui é
 * público. Nada de rota de módulo, nome de permissão ou estrutura de menu.
 */
(function () {
    "use strict";

    /* O prefixo do proxy: o portal atende em /portal-spare, e a marca é
       injetada na entrega por `com_prefixo`. Vazia, o portal está na raiz. */
    var marca = document.querySelector('meta[name="app-base"]');
    var BASE = marca ? marca.getAttribute("content") || "" : "";
    var API = BASE + "/api";
    /* Proxy que acrescenta barra no fim faria a API cair num 307 com Location
       absoluto — e http:// numa página https:// é bloqueado. Com a marca
       ligada, a URL já sai com a barra e não há o que o proxy acrescentar. */
    var API_BARRA = !!document.querySelector('meta[name="api-barra-final"]');

    function apiUrl(caminho) {
        var url = API + caminho;
        if (!API_BARRA) return url;
        var corte = url.indexOf("?");
        var base = corte === -1 ? url : url.slice(0, corte);
        var query = corte === -1 ? "" : url.slice(corte);
        if (base.charAt(base.length - 1) !== "/") base += "/";
        return base + query;
    }

    function $(sel) { return document.querySelector(sel); }

    // ── Versão e ambiente ──────────────────────────────────────────────
    // Vêm de /api/versao, que é público e devolve só o essencial. Se falhar,
    // ficam os textos padrão do HTML — a tela de login não pode depender
    // disso para abrir.
    var ROTULO = {
        producao: "Produção", testes: "Testes", teste: "Testes",
        homologacao: "Homologação", homolog: "Homologação"
    };

    function carregarVersao() {
        fetch(apiUrl("/versao"), { credentials: "same-origin" })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (v) {
                if (!v) return;
                var rotulo = ROTULO[String(v.ambiente || "producao").toLowerCase()]
                             || String(v.ambiente || "producao");
                var selo = $("#login-ambiente-texto");
                if (selo) selo.textContent = rotulo + " · rede interna";
                var ver = $("#login-versao");
                if (ver && v.commit_curto) {
                    ver.textContent = "Portal de Operações · " + v.commit_curto;
                }
            })
            .catch(function () {});
    }

    // ── Erro ───────────────────────────────────────────────────────────
    function mostrarErro(msg) {
        var caixa = $("#login-error");
        var texto = $("#login-error-text");
        if (texto) texto.textContent = msg;
        if (caixa) caixa.hidden = false;
    }

    function limparErro() {
        var caixa = $("#login-error");
        if (caixa) caixa.hidden = true;
    }

    /* O 422 do FastAPI vem como LISTA de erros de campo. Jogar isso num
       textContent virava "[object Object]" na cara do operador; aqui vira
       frase, sem o prefixo técnico do pydantic. */
    function mensagemDoErro(d, padrao) {
        if (!d) return padrao;
        if (Array.isArray(d.detail)) {
            return d.detail.map(function (it) {
                return String((it && it.msg) || it).replace(/^Value error,\s*/, "");
            }).join(" ") || padrao;
        }
        return d.detail || padrao;
    }

    // ── Entrar ─────────────────────────────────────────────────────────
    function ligarFormulario() {
        var form = $("#login-form");
        if (!form) return;
        var botao = $("#login-submit");
        var enviando = false;

        form.addEventListener("submit", function (e) {
            e.preventDefault();
            // Trava de duplo envio: dois cliques rápidos mandavam duas
            // tentativas, e uma senha errada contava DUAS falhas de login
            // no AD — o dobro da velocidade para bloquear a conta.
            if (enviando) return;
            enviando = true;
            limparErro();
            if (botao) { botao.disabled = true; botao.textContent = "Entrando…"; }

            // Entrada única pelo Logon AD desde que o login local saiu.
            fetch(apiUrl("/auth/login"), {
                method: "POST",
                credentials: "include",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    username: $("#login-username").value.trim(),
                    password: $("#login-password").value,
                    auth_type: "SSO"
                })
            }).then(function (r) {
                return r.json().catch(function () { return null; })
                        .then(function (d) { return { ok: r.ok, dados: d }; });
            }).then(function (res) {
                if (!res.ok) throw new Error(mensagemDoErro(res.dados, "Erro na requisição."));
                /* Recarrega em vez de trocar a tela: o portal é OUTRA página,
                   e é o servidor que a entrega — agora com a sessão na mão.
                   É isso que impede o menu de existir no navegador de quem
                   não entrou. `replace` para o voltar não cair no login. */
                window.location.replace(BASE + "/");
            }).catch(function (x) {
                mostrarErro(x.message || "Erro na requisição.");
                enviando = false;
                if (botao) { botao.disabled = false; botao.textContent = "Entrar"; }
            });
        });
    }

    carregarVersao();
    ligarFormulario();
})();
