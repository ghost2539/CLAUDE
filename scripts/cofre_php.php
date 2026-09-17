<?php
/**
 * Ponte para o cofre pelo PHP — usada quando o loader Python não alcança.
 *
 * Por que existe: o cofre é do time de infraestrutura e o acesso de
 * referência que eles mantêm é em PHP. Se o PHP lê e o Python não, o portal
 * não precisa de código novo: o `core/cofre.py` já resolve segredo por
 * comando externo. Esta é a ponte.
 *
 *     VCREPORTS_SECRETS_CMD="/usr/bin/php /caminho/scripts/cofre_php.php {chave}"
 *
 * Imprime SÓ o valor, sem quebra de linha extra e sem rótulo — é o que o
 * `_por_comando()` espera. Chave inexistente sai vazia com código 1, para o
 * portal saber que não achou em vez de guardar uma string vazia.
 *
 * Uso avulso, para conferir sem revelar nada:
 *     php scripts/cofre_php.php CORREIOS_USUARIO --tamanho
 */

$loader = getenv('VCREPORTS_SECRETS_PHP') ?: '/usr/local/lib/vcreports/secrets.php';
$chave = $argv[1] ?? '';
$so_tamanho = in_array('--tamanho', $argv, true);

if ($chave === '' || !preg_match('/^[A-Za-z0-9_.-]{1,64}$/', $chave)) {
    fwrite(STDERR, "Informe uma chave válida. Ex.: CORREIOS_USUARIO\n");
    exit(2);
}
if (!is_readable($loader)) {
    fwrite(STDERR, "Loader não legível por este usuário: $loader\n");
    exit(3);
}
require_once $loader;

// O time pode ter batizado a função de mais de um jeito; aceita as três.
$valor = null;
foreach (['secret', 's', 'vcreports_secret'] as $fn) {
    if (function_exists($fn)) {
        $valor = $fn($chave);
        break;
    }
}
if ($valor === null && defined($chave)) {
    $valor = constant($chave);   // cofre em define(), como no config.php do time
}
if ($valor === null || $valor === '') {
    fwrite(STDERR, "Sem valor para '$chave' neste cofre.\n");
    exit(1);
}
// Nunca imprime o valor quando se pediu só a conferência.
echo $so_tamanho ? strlen((string)$valor) . " caracteres\n" : (string)$valor;
