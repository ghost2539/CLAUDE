/* Painel de Obsolescência do Parque.

   Cada recorte usa a forma que o dado pede: número isolado vira stat tile,
   ranking vira barra horizontal de série única, e lista de aparelho vira
   tabela — lista de registro não é gráfico. Cor só onde carrega significado:
   o trilho é neutro, e obsoleto/atraso são STATUS, não categoria. */
(function () {
    'use strict';

    var alvo = document.getElementById('obs-conteudo');
    var ehAdmin = false;

    function esc(s) {
        return String(s == null ? '' : s).replace(/[&<>'"]/g, function (c) {
            return { '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' }[c];
        });
    }

    /* Nem todo erro volta em JSON: um 500 sai como texto puro do servidor,
       e o r.json() estoura com "unexpected token" — mensagem que não diz
       nada a quem está tentando usar a tela. Aqui lemos como texto e só
       então tentamos interpretar, para o motivo real chegar na tela. */
    function resposta(r) {
        return r.text().then(function (t) {
            var j = null;
            try { j = t ? JSON.parse(t) : null; } catch (_) { j = null; }
            if (r.ok) return j || {};
            var motivo = (j && (j.detail || j.erro)) || (t || '').trim().slice(0, 220) ||
                         'sem detalhe do servidor';
            throw new Error('HTTP ' + r.status + ' — ' + motivo);
        });
    }

    function num(n) {
        return (n == null ? 0 : n).toLocaleString('pt-BR');
    }

    function quando(iso) {
        if (!iso) return '—';
        var d = new Date(iso);
        return isNaN(d) ? '—' : d.toLocaleString('pt-BR', { dateStyle: 'short', timeStyle: 'short' });
    }

    function kpi(valor, rotulo, nota, tom) {
        return '<div class="obs-kpi' + (tom ? ' obs-kpi--' + tom : '') + '">' +
            '<div class="obs-kpi-valor">' + valor + '</div>' +
            '<div class="obs-kpi-rotulo">' + esc(rotulo) + '</div>' +
            (nota ? '<div class="obs-kpi-nota">' + esc(nota) + '</div>' : '') +
            '</div>';
    }

    /* Barra: o total é o trilho; a parte crítica sai destacada dentro dele.
       Obsoleto é subconjunto do total, então é um segmento — não uma
       segunda barra ao lado, que faria parecer grandezas independentes. */
    function barra(nome, valor, critico, maximo) {
        var pct = maximo > 0 ? (valor / maximo) * 100 : 0;
        var pctCrit = maximo > 0 ? (critico / maximo) * 100 : 0;
        var resto = Math.max(0, pct - pctCrit);
        return '<div class="obs-barra-linha">' +
            '<div class="obs-barra-topo">' +
                '<span class="obs-barra-nome">' + esc(nome) + '</span>' +
                '<span class="obs-barra-val">' + num(valor) +
                    (critico > 0 ? ' · ' + num(critico) + ' obsoleto(s)' : '') + '</span>' +
            '</div>' +
            '<div class="obs-barra-trilho">' +
                (pctCrit > 0 ? '<div class="obs-barra-fill obs-barra-fill--critico" style="width:' + pctCrit + '%"></div>' : '') +
                (resto > 0 ? '<div class="obs-barra-fill" style="width:' + resto + '%"></div>' : '') +
            '</div></div>';
    }

    function cardBarras(titulo, sub, itens, campoNome, campoValor, campoCritico) {
        if (!itens || !itens.length) return '';
        var maximo = Math.max.apply(null, itens.map(function (x) { return x[campoValor] || 0; }));
        var linhas = itens.map(function (x) {
            return barra(x[campoNome], x[campoValor] || 0, x[campoCritico] || 0, maximo);
        }).join('');
        return '<div class="obs-card"><h2>' + esc(titulo) + '</h2>' +
            '<p class="obs-sub-card">' + esc(sub) + '</p>' +
            '<div class="obs-barras">' + linhas + '</div>' +
            '<div class="obs-legenda">' +
                '<span><i style="background:var(--barra-fundo)"></i>No parque</span>' +
                '<span><i style="background:var(--status-critico)"></i>Obsoleto</span>' +
            '</div></div>';
    }

    function cardTags(tags) {
        if (!tags || !tags.length) {
            return '<div class="obs-card"><h2>Tags</h2>' +
                '<p class="obs-sub-card">Nenhum coletor com as tags acompanhadas.</p></div>';
        }
        var maximo = Math.max.apply(null, tags.map(function (t) { return t.quantidade; }));
        var linhas = tags.map(function (t) {
            return barra(t.tag + ' · ' + t.grupo, t.quantidade, 0, maximo);
        }).join('');
        return '<div class="obs-card"><h2>Coletores por tag</h2>' +
            '<p class="obs-sub-card">Só aparecem as tags que têm coletor atribuído.</p>' +
            '<div class="obs-barras">' + linhas + '</div></div>';
    }

    function cardAntigos(lista, idadeDesconhecida) {
        if (!lista || !lista.length) {
            return '<div class="obs-card"><h2>Coletores mais antigos</h2>' +
                '<p class="obs-sub-card">Nenhum coletor com idade conhecida. A data de ' +
                'aquisição vem do EBS; sem ela a idade não é estimada, para não ' +
                'inventar número.</p></div>';
        }
        var linhas = lista.slice(0, 20).map(function (c) {
            return '<tr><td>' + esc(c.nome || c.mdm_id) + '</td>' +
                '<td>' + esc(c.bu || '—') + '</td>' +
                '<td>' + esc(c.loja || '—') + '</td>' +
                '<td>' + esc(c.modelo || '—') + '</td>' +
                '<td>' + esc(c.versao_os || '—') + '</td>' +
                '<td class="obs-num">' + (c.idade_anos != null ? c.idade_anos.toFixed(1) : '—') + '</td></tr>';
        }).join('');
        return '<div class="obs-card"><h2>Coletores mais antigos</h2>' +
            '<p class="obs-sub-card">' +
                (idadeDesconhecida ? num(idadeDesconhecida) + ' sem data de aquisição no EBS ficaram de fora.' : '') +
            '</p><div class="obs-tabela-wrap"><table class="obs-tabela"><thead><tr>' +
            '<th>Coletor</th><th>BU</th><th>Loja</th><th>Modelo</th><th>Android</th>' +
            '<th class="obs-num">Anos</th></tr></thead><tbody>' + linhas +
            '</tbody></table></div></div>';
    }

    function cardLojas(lojas) {
        if (!lojas || !lojas.length) return '';
        var linhas = lojas.slice(0, 25).map(function (l) {
            return '<tr><td>' + esc(l.bu_nome || l.bu) + '</td>' +
                '<td>' + esc(l.loja) + '</td>' +
                '<td class="obs-num">' + num(l.coletores) + '</td>' +
                '<td class="obs-num">' + (l.obsoletos ? '<span class="obs-etq obs-etq--critico">' + num(l.obsoletos) + '</span>' : '—') + '</td>' +
                '<td class="obs-num">' + (l.sem_ver ? '<span class="obs-etq obs-etq--alerta">' + num(l.sem_ver) + '</span>' : '—') + '</td></tr>';
        }).join('');
        return '<div class="obs-card"><h2>Lojas com mais coletores</h2>' +
            '<p class="obs-sub-card">As 25 maiores, de ' + num(lojas.length) + ' lojas com parque.</p>' +
            '<div class="obs-tabela-wrap"><table class="obs-tabela"><thead><tr>' +
            '<th>BU</th><th>Loja</th><th class="obs-num">Coletores</th>' +
            '<th class="obs-num">Obsoletos</th><th class="obs-num">Sem comunicar</th>' +
            '</tr></thead><tbody>' + linhas + '</tbody></table></div></div>';
    }

    function acoes(msg) {
        if (!ehAdmin) return '';
        return '<div class="obs-acoes">' +
            '<button id="obs-coletar" class="obs-btn">Atualizar do MDM</button>' +
            '<button id="obs-cred" class="obs-btn obs-btn--secundario">Credencial do MDM</button>' +
            '<span id="obs-msg" class="obs-msg">' + esc(msg || '') + '</span></div>';
    }

    /* Formulário da credencial de serviço. Fica aqui, e não só no CLI do
       cofre, porque exigir acesso ao shell do servidor para ligar o módulo
       deixaria a entrega pela metade. A senha vai para o cofre e nunca
       volta: o formulário só informa se está configurada e qual o usuário. */
    function formCredencial(estado) {
        var cfg = estado && estado.configurada;
        // Cofre sem permissão de escrita trava tudo: avisa aqui, com a linha
        // pronta, em vez de deixar a pessoa descobrir depois de digitar a senha.
        var travado = estado && estado.cofre_gravavel === false && !estado.cofre_corporativo;
        var aviso = '';
        if (travado) {
            aviso = '<div class="obs-erro" style="margin:0 0 16px">' +
                '<b>O cofre não é gravável.</b><br>Pasta atual: <code>' +
                esc(estado.cofre_pasta) + '</code>' +
                (estado.cofre_sugestao_ok
                    ? '<br><br>A pasta da aplicação é somente leitura, e deve ser. ' +
                      'Aponte o cofre para a área gravável do projeto — no ' +
                      '<code>data/environment</code>:<br><br><code>PORTAL_COFRE_DIR=' +
                      esc(estado.cofre_sugestao) + '</code><br><br>Depois reinicie o serviço.'
                    : '<br><br>Nenhuma pasta gravável encontrada. Ajuste a permissão ' +
                      'da pasta acima para o usuário que roda o portal.') +
                '</div>';
        }
        return '<div class="obs-card obs-cred-card">' +
            '<h2>Credencial de serviço do MDM</h2>' +
            '<p class="obs-sub-card">' +
                (cfg ? 'Configurada para <b>' + esc(estado.usuario) + '</b>. Salvar de novo substitui.'
                     : 'Ainda não configurada — sem ela a coleta não roda.') +
            '</p>' + aviso +
            '<div class="obs-campos">' +
                '<label>Usuário<input id="obs-cred-user" type="text" autocomplete="off" ' +
                    'placeholder="renner\\seulogin" value="' + esc((estado && estado.usuario) || '') + '"></label>' +
                '<label>Senha<input id="obs-cred-senha" type="password" autocomplete="new-password" ' +
                    'placeholder="a mesma do ServiceNow"></label>' +
            '</div>' +
            '<p class="obs-kpi-nota" style="margin:10px 0 14px">' +
                'É uma conta de <b>serviço</b>, não a sua sessão: a coleta roda sem ninguém logado. ' +
                'A senha vai cifrada para o cofre e nunca é exibida de volta.</p>' +
            '<div class="obs-acoes" style="margin:0">' +
                '<button id="obs-cred-salvar" class="obs-btn">Salvar no cofre</button>' +
                '<button id="obs-cred-fechar" class="obs-btn obs-btn--secundario">Fechar</button>' +
                '<span id="obs-cred-msg" class="obs-msg"></span>' +
            '</div></div>';
    }

    function abrirCredencial() {
        fetch('/api/obsolescencia/credencial', { credentials: 'include' })
            .then(resposta)
            .catch(function () { return {}; })
            .then(function (estado) {
                var caixa = document.getElementById('obs-cred-box');
                if (!caixa) {
                    caixa = document.createElement('div');
                    caixa.id = 'obs-cred-box';
                    alvo.insertBefore(caixa, alvo.firstChild.nextSibling);
                }
                caixa.innerHTML = formCredencial(estado);
                document.getElementById('obs-cred-fechar')
                    .addEventListener('click', function () { caixa.remove(); });
                document.getElementById('obs-cred-salvar')
                    .addEventListener('click', function () { salvarCredencial(caixa); });
            });
    }

    function salvarCredencial(caixa) {
        var u = document.getElementById('obs-cred-user').value.trim();
        var p = document.getElementById('obs-cred-senha').value;
        var m = document.getElementById('obs-cred-msg');
        var b = document.getElementById('obs-cred-salvar');
        if (!u || !p) { m.textContent = 'Informe usuário e senha.'; return; }
        b.disabled = true;
        m.textContent = 'Guardando no cofre…';
        fetch('/api/obsolescencia/credencial', {
            method: 'POST', credentials: 'include',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ usuario: u, senha: p })
        })
            .then(resposta)
            .then(function () {
                b.disabled = false;
                document.getElementById('obs-cred-senha').value = '';
                m.textContent = 'Guardada. Já pode usar “Atualizar do MDM”.';
                setTimeout(function () { caixa.remove(); }, 2200);
            })
            .catch(function (e) { b.disabled = false; m.textContent = e.message; });
    }

    function pintar(d) {
        var semVer = d.sem_ver || {};
        alvo.innerHTML =
            acoes('Última coleta: ' + quando(d.coletado_em)) +
            '<div class="obs-kpis">' +
                kpi(num(d.total), 'Coletores no parque',
                    d.fora_de_loja ? num(d.fora_de_loja) + ' fora de loja (CD)' : '') +
                kpi(num(d.obsoletos), 'Obsoletos',
                    'Regra: ' + (d.modo_regra === 'qualquer' ? 'qualquer critério' : 'os três critérios'),
                    'critico') +
                kpi(num(semVer.quantidade), 'Sem comunicar',
                    'Há mais de ' + (semVer.limite_dias || 30) + ' dias', 'alerta') +
                kpi(num(d.em_tratativa), 'Em tratativa', 'Sumiram do MDM') +
            '</div>' +
            '<div class="obs-grid2">' +
                cardBarras('Parque por BU', 'Coletores em loja, com a parcela obsoleta destacada.',
                           d.por_bu, 'bu_nome', 'coletores', 'obsoletos') +
                cardTags(d.tags) +
            '</div>' +
            '<div class="obs-grid2">' +
                cardLojas(d.por_loja) +
                cardAntigos(d.mais_antigos, d.idade_desconhecida) +
            '</div>';
        ligarBotao();
    }

    function pendente(msg) {
        alvo.innerHTML = acoes('') +
            '<div class="obs-aviso">' +
                '<h2>Nenhuma coleta ainda</h2>' +
                '<p>' + esc(msg || 'O painel está no ar, mas o parque ainda não foi lido.') + '</p>' +
                (ehAdmin ? '<p>Use <b>Atualizar do MDM</b> acima para fazer a primeira leitura. ' +
                    'São 15,8 mil coletores em ~159 páginas, então leva alguns minutos.</p>'
                    : '<p>Peça a um administrador do portal para rodar a primeira coleta.</p>') +
            '</div>';
        ligarBotao();
    }

    function erro(msg) {
        alvo.innerHTML = acoes('') + '<div class="obs-erro">' + esc(msg) + '</div>';
        ligarBotao();
    }

    function ligarBotao() {
        var c = document.getElementById('obs-cred');
        if (c) c.addEventListener('click', abrirCredencial);
        var b = document.getElementById('obs-coletar');
        if (!b) return;
        b.addEventListener('click', function () {
            var m = document.getElementById('obs-msg');
            b.disabled = true;
            if (m) m.textContent = 'Lendo o parque no MDM… isso leva alguns minutos.';
            fetch('/api/obsolescencia/coletar', { method: 'POST', credentials: 'include' })
                .then(resposta)
                .then(function (j) {
                    if (m) m.textContent = 'Coleta concluída: ' + num(j.lidos) + ' lidos, ' +
                        num(j.novos) + ' novos, ' + num(j.sumiram) + ' sumiram.';
                    carregar();
                })
                .catch(function (e) {
                    b.disabled = false;
                    if (m) m.textContent = e.message;
                    // Falta credencial? Abre o formulário em vez de só reclamar.
                    if (/credencial/i.test(e.message)) abrirCredencial();
                });
        });
    }

    function carregar() {
        fetch('/api/obsolescencia/resumo', { credentials: 'include' })
            .then(function (r) {
                if (r.status === 401) { location.href = '/?next=/obsolescencia'; return null; }
                return resposta(r);
            })
            .then(function (d) {
                if (!d) return;
                var u = document.getElementById('obs-usuario');
                if (u && d.usuario) u.textContent = d.usuario;
                var f = document.getElementById('obs-fonte');
                if (f && d.fonte) f.textContent = d.fonte;
                if (d.erro) { erro(d.erro); return; }
                if (d.pendente) { pendente(); return; }
                pintar(d);
            })
            .catch(function (e) { erro(e.message); });
    }

    // Só admin vê o botão de coletar; o painel em si é de quem tem login.
    fetch('/api/auth/me', { credentials: 'include' })
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (u) { ehAdmin = !!(u && u.is_admin); })
        .catch(function () {})
        .then(carregar);
})();
