/* ================================================================
   Módulo: Internalização (menu Entrada) — placeholder
   A etapa que recebe os agendamentos após "Confirmar recebimento".
   Fluxo a configurar; a tela existe para já ocupar o lugar no menu.
   ================================================================ */
window.SPARE_MODULES = window.SPARE_MODULES || {};
window.SPARE_MODULES.internalizacao = {
    async render(container) {
        var S = window.SPARE;
        container.innerHTML =
            '<h1 class="page-title">Internalização</h1>' +
            '<div class="card"><div class="card-body">' +
            '<p class="text-muted" style="margin:0">Etapa em construção. Os ' +
            'agendamentos com <b>recebimento confirmado</b> em Agendamentos Forn. ' +
            'chegam aqui para internalização. O fluxo será configurado em seguida.</p>' +
            '</div></div>';
    }
};
