/*
 * Lançador do cliente Oracle Forms do EBS — substitui o Java Web Start.
 *
 * Por que existe: o OpenJDK 21 do servidor não tem javaws, e o cliente Forms
 * é um applet. Este programa faz o papel mínimo do navegador/JWS: lê o
 * arquivo .jnlp, baixa os jars, instancia o applet com os parâmetros do jnlp
 * e o exibe numa janela. Como o applet roda DENTRO desta JVM, ganhamos de
 * graça o que um RPA precisa e que de fora exigiria xdotool/xclip/ImageMagick:
 *
 *   - teclado via java.awt.Robot (funciona no Xvfb, extensão XTEST);
 *   - área de transferência via Toolkit (ler o valor de um campo é
 *     Home, Shift+End, Ctrl+C, ler clipboard);
 *   - captura de tela em PNG;
 *   - a URL que o Forms tenta abrir no navegador (Arquivo → Exportar,
 *     anexos) chega em showDocument() e é apenas informada — o Python baixa
 *     com a sessão HTTP dele.
 *
 * Protocolo: uma ordem por linha na entrada padrão, uma resposta por linha
 * na saída padrão, sempre começando com "OK", "ERRO" ou "EVENTO". Detalhes
 * em integracoes/ebs_forms.py (lado Python), que é quem manda aqui.
 *
 * Compilado pelo scripts/ebs_forms_preparar.sh para data/ebs_forms/bin.
 * java.applet está marcado para remoção no JDK, mas existe no 21; se um dia
 * sumir, o cliente Forms também deixa de rodar em qualquer lugar.
 */
import java.applet.Applet;
import java.applet.AppletContext;
import java.applet.AppletStub;
import java.applet.AudioClip;
import java.awt.BorderLayout;
import java.awt.Dimension;
import java.awt.Frame;
import java.awt.Image;
import java.awt.Rectangle;
import java.awt.Robot;
import java.awt.Toolkit;
import java.awt.Window;
import java.awt.datatransfer.Clipboard;
import java.awt.datatransfer.DataFlavor;
import java.awt.datatransfer.StringSelection;
import java.awt.event.KeyEvent;
import java.awt.image.BufferedImage;
import java.io.BufferedReader;
import java.io.File;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.io.PrintStream;
import java.lang.reflect.Field;
import java.net.URL;
import java.net.URLClassLoader;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.nio.file.StandardCopyOption;
import java.util.ArrayList;
import java.util.Base64;
import java.util.Enumeration;
import java.util.Iterator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import javax.imageio.ImageIO;
import javax.xml.parsers.DocumentBuilderFactory;
import org.w3c.dom.Document;
import org.w3c.dom.Element;
import org.w3c.dom.NodeList;

public class LancadorForms {

    // Saída de protocolo separada do System.out original: bibliotecas do
    // Forms escrevem no stdout e isso confundiria o lado Python.
    private static PrintStream proto;
    private static Robot robo;
    private static Frame janela;
    private static Applet applet;
    private static final Map<String, String> params = new LinkedHashMap<>();
    private static String codebase = "";
    private static String mainClass = "";
    private static String dirJars = "/OA_JAVA/oracle/apps/fnd/jar/";
    private static int larg = 1280, alt = 900;
    // "java": teclas injetadas na fila de eventos do AWT (não passam pelo X —
    // obrigatório no Weston headless, cujo Xwayland aborta ao receber XTEST
    // sem seat). "robot": XTEST via java.awt.Robot (Xvfb/Xvnc).
    private static final boolean ENTRADA_JAVA = !"robot".equalsIgnoreCase(System.getProperty("forms.entrada", "java"));

    public static void main(String[] args) throws Exception {
        if (args.length < 2) {
            System.err.println("uso: LancadorForms <arquivo.jnlp> <pasta-cache-jars> [largura] [altura]");
            System.exit(2);
        }
        proto = new PrintStream(new java.io.FileOutputStream(java.io.FileDescriptor.out), true, "UTF-8");
        // Tudo que o Forms imprimir vai para stderr; o stdout fica só para o protocolo.
        System.setOut(System.err);
        vigiarExit();

        Path jnlp = Paths.get(args[0]);
        Path cache = Paths.get(args[1]);
        if (args.length >= 4) { larg = Integer.parseInt(args[2]); alt = Integer.parseInt(args[3]); }
        Files.createDirectories(cache);

        List<String> jars = lerJnlp(jnlp);
        List<URL> urls = new ArrayList<>();
        for (String j : jars) urls.add(baixarJar(j, cache));
        // Além dos jars, o EBS carrega classes SOLTAS do codebase (é para isso
        // que o jnlp declara codebase=".../OA_JAVA/"): NLSUtil, CommBean e
        // companhia só existem lá. Um URLClassLoader com uma URL terminada em
        // "/" busca cada classe pelo caminho do pacote, como o navegador fazia.
        urls.add(new URL(codebase));
        // Jars que o jnlp não declara mas o Forms precisa (ex.: fndi18n.jar,
        // com NLSUtil). Nome curto vale: resolvemos no mesmo diretório dos
        // jars do jnlp — é o que scripts/ebs_forms_achar_classe.sh sugere.
        String extras = System.getProperty("forms.jars.extra", "");
        for (String extra : extras.split(",")) {
            extra = extra.trim();
            if (extra.isEmpty()) continue;
            if (!extra.matches("[A-Za-z0-9._/-]+\\.jar")) {
                throw new IllegalArgumentException(
                    "EBS_FORMS_JARS_EXTRA tem um valor inválido: '" + extra + "'. "
                    + "Esperado nome de jar (ex.: fndi18n.jar) ou caminho terminando em .jar.");
            }
            if (!extra.contains("/")) extra = dirJars + extra;
            urls.add(baixarJar(extra, cache));
        }
        responder("EVENTO jars " + urls.size() + " (inclui codebase " + codebase + ")");

        // A classe é a que o jnlp declara (no EBS, FndFormsEngine — é ela quem
        // trata ticket e sessão do Web Start). -Dforms.classe força outra.
        String classe = System.getProperty("forms.classe", "");
        if (classe.isEmpty()) classe = mainClass.isEmpty() ? "oracle.forms.engine.Main" : mainClass;
        registrarServicosJnlp();
        URLClassLoader cl = new URLClassLoader(urls.toArray(new URL[0]), LancadorForms.class.getClassLoader());
        Thread.currentThread().setContextClassLoader(cl);
        applet = (Applet) cl.loadClass(classe).getDeclaredConstructor().newInstance();
        applet.setStub(new Stub());

        janela = new Frame("LancadorForms");
        janela.addWindowListener(new java.awt.event.WindowAdapter() {
            public void windowClosing(java.awt.event.WindowEvent e) { responder("EVENTO janela-lancador closing"); }
            public void windowClosed(java.awt.event.WindowEvent e) { responder("EVENTO janela-lancador closed"); }
            public void windowIconified(java.awt.event.WindowEvent e) { responder("EVENTO janela-lancador iconified"); }
        });
        janela.setLayout(new BorderLayout());
        janela.add(applet, BorderLayout.CENTER);
        janela.setSize(larg, alt);
        janela.setLocation(0, 0);
        applet.setPreferredSize(new Dimension(larg, alt));
        janela.setVisible(true);
        robo = new Robot();
        robo.setAutoDelay(30);
        robo.setAutoWaitForIdle(false);
        applet.init();
        // start() do cliente Forms pode não voltar (fica no laço da sessão);
        // roda à parte para o protocolo não ficar refém dele. Se falhar, a
        // JVM encerra com a causa no log e o lado Python enxerga a queda.
        Thread inicio = new Thread(() -> {
            try {
                applet.start();
                responder("EVENTO start-retornou");
            } catch (Throwable t) {
                t.printStackTrace();
                responder("EVENTO falha-start " + t);
                saidaAutorizada = true;
                System.exit(3);
            }
        }, "forms-start");
        inicio.setDaemon(true);
        inicio.start();
        Thread.sleep(1500);
        // A janela já estava visível quando o applet montou os componentes:
        // sem validate() nada é desenhado. E sem gerenciador de janelas no
        // Xvfb ninguém dá o foco — pedimos nós.
        if (Boolean.getBoolean("forms.depurar")) {
            // Registra no stderr cada tecla que chega à fila de eventos e para quem.
            Toolkit.getDefaultToolkit().addAWTEventListener(ev -> {
                if (ev instanceof KeyEvent && ev.getID() == KeyEvent.KEY_PRESSED)
                    System.err.println("TECLA " + KeyEvent.getKeyText(((KeyEvent) ev).getKeyCode())
                            + " -> " + ev.getSource().getClass().getName());
            }, java.awt.AWTEvent.KEY_EVENT_MASK);
        }
        // Sem esperar a fila de eventos entre pressionar e soltar: com o
        // auto-repeat do X, um Ctrl+V "segurado" colava dezenas de vezes
        // (por isso o Robot nasce com autoDelay 30 e sem waitForIdle).
        focar();
        pronto = true;
        responder("OK pronto classe=" + classe);

        BufferedReader in = new BufferedReader(new InputStreamReader(System.in, StandardCharsets.UTF_8));
        String linha;
        while ((linha = in.readLine()) != null) {
            linha = linha.trim();
            if (linha.isEmpty()) continue;
            try {
                if (!executar(linha)) break;
            } catch (Exception e) {
                responder("ERRO " + e.getClass().getSimpleName() + ": " + String.valueOf(e.getMessage()).replace('\n', ' '));
            }
        }
        encerrar();
    }

    // ── ordens ──────────────────────────────────────────────────────────
    private static boolean executar(String linha) throws Exception {
        String[] p = linha.split(" ", 2);
        String cmd = p[0].toLowerCase();
        String arg = p.length > 1 ? p[1] : "";
        switch (cmd) {
            case "sair":
                responder("OK tchau");
                return false;
            case "ping":
                responder("OK pong");
                break;
            case "esperar":
                Thread.sleep(Long.parseLong(arg.trim()));
                responder("OK");
                break;
            case "tecla":
                // ex.: tecla CTRL+F11 | tecla TAB | tecla SHIFT+END
                for (String combo : arg.trim().split("\\s+")) tecla(combo);
                responder("OK");
                break;
            case "texto":
                // Digita via área de transferência + Ctrl+V: o Robot não sabe
                // digitar acento e o Forms aceita colar. Argumento em base64
                // para sobreviver a espaços e quebras de linha.
                String txt = new String(Base64.getDecoder().decode(arg.trim()), StandardCharsets.UTF_8);
                if (ENTRADA_JAVA) {
                    for (char c : txt.toCharArray()) digitarCharJava(c);
                    sincronizar();
                } else {
                    clipboard(txt);
                    tecla("CTRL+V");
                }
                responder("OK");
                break;
            case "digitar":
                // Só ASCII, tecla a tecla — para números e códigos, é mais
                // fiel ao que uma pessoa faz e dispara as validações do campo.
                String s = new String(Base64.getDecoder().decode(arg.trim()), StandardCharsets.UTF_8);
                for (char c : s.toCharArray()) { if (ENTRADA_JAVA) digitarCharJava(c); else digitarChar(c); }
                sincronizar();
                responder("OK");
                break;
            case "copiar":
                // Primeiro sem X: getText() do componente focado. Só se não
                // der, seleciona e copia pela área de transferência.
                String direto = lerCampoFocado();
                if (direto != null) {
                    responder("OK " + Base64.getEncoder().encodeToString(direto.getBytes(StandardCharsets.UTF_8)));
                    break;
                }
                clipboard(" vazio ");
                tecla("HOME"); tecla("SHIFT+END"); tecla("CTRL+C");
                Thread.sleep(120);
                String v = lerClipboard();
                if (v.equals(" vazio ")) v = "";
                responder("OK " + Base64.getEncoder().encodeToString(v.getBytes(StandardCharsets.UTF_8)));
                break;
            case "clipboard":
                responder("OK " + Base64.getEncoder().encodeToString(lerClipboard().getBytes(StandardCharsets.UTF_8)));
                break;
            case "foto": {
                File f = new File(arg.trim());
                if (f.getParentFile() != null) f.getParentFile().mkdirs();
                ImageIO.write(capturar(), "png", f);
                responder("OK " + f.getAbsolutePath());
                break;
            }
            case "janelas":
                StringBuilder sb = new StringBuilder();
                for (Window w : Window.getWindows()) {
                    if (!w.isShowing()) continue;
                    String t = tituloDe(w);
                    if (sb.length() > 0) sb.append('\n');
                    sb.append(w.getClass().getSimpleName()).append('=').append(t == null ? "" : t);
                }
                responder("OK " + Base64.getEncoder().encodeToString(sb.toString().getBytes(StandardCharsets.UTF_8)));
                break;
            case "focarcampo": {
                // Endereça o componente pelo nome do mapa (ex.: VTextField200).
                // O AWT entrega KeyEvent ao DONO DO FOCO, não ao componente de
                // origem — e o Forms gerencia foco por conta própria. Por isso
                // pedimos o foco, CONFERIMOS, e só então damos por bom; se não
                // pegou, um clique sintético (evento Java, não XTEST) resolve.
                java.awt.Component alvo = acharPorNome(arg.trim());
                if (alvo == null) { responder("ERRO campo não encontrado: " + arg.trim()); break; }
                final java.awt.Component a = alvo;
                final java.awt.Component anterior = alvoAtual;
                naEDT(() -> {
                    // Um clique de verdade é o que o Forms entende como "entrei
                    // neste item": ele move o cursor e prepara a validação.
                    cliqueSintetico(a);
                    a.requestFocusInWindow();
                    // Sem gerenciador de janelas não há foco real; avisamos o
                    // componente diretamente para ele desenhar o cursor e
                    // aceitar digitação.
                    if (anterior != null && anterior != a)
                        anterior.dispatchEvent(new java.awt.event.FocusEvent(anterior, java.awt.event.FocusEvent.FOCUS_LOST, false, a));
                    a.dispatchEvent(new java.awt.event.FocusEvent(a, java.awt.event.FocusEvent.FOCUS_GAINED, false, anterior));
                    return null;
                }, 8000);
                alvoAtual = a;
                sincronizar();
                responder("OK alvo=" + arg.trim() + " foco=" + donoDoFoco());
                break;
            }
            case "lercampo": {
                java.awt.Component alvo = acharPorNome(arg.trim());
                if (alvo == null) { responder("ERRO campo não encontrado: " + arg.trim()); break; }
                final java.awt.Component a = alvo;
                String lido = naEDT(() -> {
                    String t = textoDe(a);
                    return t == null ? "" : t;
                }, 5000);
                responder("OK " + Base64.getEncoder().encodeToString(lido.getBytes(StandardCharsets.UTF_8)));
                break;
            }
            case "focoatual":
                responder("OK " + donoDoFoco());
                break;
            case "clicar": {
                // Botões do EBS (oracle.apps.fnd.ui.Button) e do EWT expõem
                // doClick(); quando não expõem, mandamos os eventos de mouse.
                java.awt.Component alvo = acharPorNome(arg.trim());
                if (alvo == null) { responder("ERRO botão não encontrado: " + arg.trim()); break; }
                final java.awt.Component a = alvo;
                String como = naEDT(() -> {
                    try {
                        a.getClass().getMethod("doClick").invoke(a);
                        return "doClick";
                    } catch (Exception semDoClick) {
                        cliqueSintetico(a);
                        return "eventos de mouse";
                    }
                }, 8000);
                sincronizar();
                responder("OK " + como);
                break;
            }
            case "dialogo": {
                // Avisos do Forms ("Precaução: O ATIVO SELECIONADO ESTÁ BAIXADO!")
                // são janelas com um texto e botões. Devolvemos o que dizem para
                // o roteiro decidir — e o dado vira campo no resultado.
                String d = naEDT(LancadorForms::dialogoJson, 10000);
                responder("OK " + Base64.getEncoder().encodeToString(d.getBytes(StandardCharsets.UTF_8)));
                break;
            }
            case "itensmenu": {
                // Abre um menu e devolve os itens: é assim que descobrimos o
                // texto exato de "Fechar Janela"/"Localizar" nesta instalação.
                String nomeMenu = arg.trim();
                java.awt.Component m = naEDT(() -> acharPorTexto(nomeMenu), 8000);
                if (m == null) { responder("ERRO menu não encontrado: " + nomeMenu); break; }
                final java.awt.Component mm = m;
                naEDT(() -> {
                    try { mm.getClass().getMethod("doClick").invoke(mm); }
                    catch (Exception e) { cliqueSintetico(mm); }
                    return null;
                }, 8000);
                Thread.sleep(500);
                String itens = naEDT(() -> {
                    StringBuilder j = new StringBuilder("[");
                    boolean primeiroItem = true;
                    for (Window w : Window.getWindows()) {
                        if (!w.isShowing()) continue;
                        for (java.awt.Component f : todosOsComponentes(w, new ArrayList<>())) {
                            if (!f.isShowing()) continue;
                            String cl = f.getClass().getName();
                            if (!cl.contains("MenuItem") && !cl.contains("Menu$")) continue;
                            String t = textoDe(f);
                            if (t == null || t.isBlank()) continue;
                            if (!primeiroItem) j.append(',');
                            primeiroItem = false;
                            j.append(jsonTexto(t.trim()));
                        }
                    }
                    return j.append(']').toString();
                }, 10000);
                tecla("ESC");   // não deixa o menu aberto atrapalhando
                sincronizar();
                responder("OK " + Base64.getEncoder().encodeToString(itens.getBytes(StandardCharsets.UTF_8)));
                break;
            }
            case "fecharjanela": {
                // O X do Forms é desenhado, não é componente. O caminho que
                // existe de verdade é o menu do sistema da janela (o ícone à
                // esquerda na barra de título), que tem o item "Fechar".
                String titulo = arg.trim();
                String como = "não tentado";
                java.awt.Component menuSistema = naEDT(() -> {
                    List<java.awt.Container> quadros = new ArrayList<>();
                    for (Window w : Window.getWindows()) if (w.isShowing()) acharQuadros(w, quadros);
                    for (java.awt.Container q : quadros) {
                        if (!tituloDoQuadro(q).toLowerCase().contains(titulo.toLowerCase())) continue;
                        for (java.awt.Component f : todosOsComponentes(q, new ArrayList<>()))
                            if (f.getClass().getName().contains("SystemMenu") && f.isShowing()) return f;
                    }
                    return null;
                }, 10000);
                if (menuSistema != null) {
                    final java.awt.Component ms = menuSistema;
                    naEDT(() -> { cliqueSintetico(ms); return null; }, 8000);
                    Thread.sleep(400);
                    java.awt.Component itemFechar = naEDT(() -> {
                        for (Window w : Window.getWindows()) {
                            if (!w.isShowing()) continue;
                            for (java.awt.Component f : todosOsComponentes(w, new ArrayList<>())) {
                                if (!f.isShowing()) continue;
                                String t = textoDe(f);
                                if (t == null) continue;
                                String tl = t.replace("&", "").trim().toLowerCase();
                                if (tl.equals("fechar") || tl.startsWith("fechar")) return f;
                            }
                        }
                        return null;
                    }, 8000);
                    if (itemFechar != null) {
                        final java.awt.Component it = itemFechar;
                        naEDT(() -> {
                            try { it.getClass().getMethod("doClick").invoke(it); }
                            catch (Exception e) { cliqueSintetico(it); }
                            return null;
                        }, 8000);
                        como = "menu do sistema > " + textoDe(itemFechar);
                    } else {
                        tecla("ESC");
                        como = "menu do sistema aberto, sem item 'Fechar'";
                    }
                    sincronizar();
                    Thread.sleep(400);
                }
                // confirma: a janela sumiu mesmo?
                boolean aberta = condicaoAtendida("janela:" + titulo);
                if (aberta) {
                    String alternativa = System.getProperty("forms.fechar.tecla", "CTRL+F4");
                    tecla(alternativa);
                    sincronizar();
                    Thread.sleep(400);
                    aberta = condicaoAtendida("janela:" + titulo);
                    como += "; tentei " + alternativa;
                }
                responder((aberta ? "ERRO ainda aberta após: " : "OK fechada por ") + como);
                break;
            }
            case "menu": {
                // Navega no menu do Forms: "menu Verificar|Localizar". Depois de
                // uma consulta o Forms FECHA a janela Localizar Ativos; é por
                // aqui que ela volta para a consulta seguinte.
                String[] caminho = arg.split("\\|");
                StringBuilder trilha = new StringBuilder();
                for (int k = 0; k < caminho.length; k++) {
                    String item = caminho[k].trim();
                    java.awt.Component m = naEDT(() -> acharPorTexto(item), 8000);
                    if (m == null) { responder("ERRO menu: não achei '" + item + "'" + (trilha.length() > 0 ? " depois de " + trilha : "")); break; }
                    final java.awt.Component mm = m;
                    naEDT(() -> {
                        try { mm.getClass().getMethod("doClick").invoke(mm); }
                        catch (Exception e) { cliqueSintetico(mm); }
                        return null;
                    }, 8000);
                    trilha.append(trilha.length() > 0 ? " > " : "").append(item);
                    sincronizar();
                    Thread.sleep(400);   // o submenu precisa montar antes do próximo item
                    if (k == caminho.length - 1) responder("OK menu " + trilha);
                }
                break;
            }
            case "clicartexto": {
                // Clica no botão pelo RÓTULO (OK, Cancelar, Atribuições...),
                // que é estável mesmo quando o nome do componente muda.
                String rotulo = arg.trim();
                java.awt.Component alvo = naEDT(() -> acharPorTexto(rotulo), 10000);
                if (alvo == null) { responder("ERRO botão não encontrado: " + rotulo); break; }
                final java.awt.Component a = alvo;
                String como = naEDT(() -> {
                    try {
                        a.getClass().getMethod("doClick").invoke(a);
                        return "doClick";
                    } catch (Exception semDoClick) {
                        cliqueSintetico(a);
                        return "eventos de mouse";
                    }
                }, 8000);
                sincronizar();
                responder("OK " + rotulo + " (" + como + ")");
                break;
            }
            case "esperarate": {
                // Espera por CONDIÇÃO, não por relógio: sai assim que a tela
                // fica pronta, em vez de dormir um tempo fixo "para garantir".
                //   esperarate janela:Localizar Ativos 60000
                //   esperarate campo:VTextField200 30000
                //   esperarate grade 30000
                String[] pa = arg.trim().split("\\s+(?=\\d+$)");
                String cond = pa[0].trim();
                long limite = System.currentTimeMillis() + (pa.length > 1 ? Long.parseLong(pa[1]) : 30000);
                boolean pronto2 = false;
                while (System.currentTimeMillis() < limite) {
                    if (condicaoAtendida(cond)) { pronto2 = true; break; }
                    Thread.sleep(200);
                }
                if (!pronto2) { responder("ERRO esperarate: '" + cond + "' não aconteceu no prazo"); break; }
                responder("OK " + cond);
                break;
            }
            case "dados": {
                // Tudo o que a tela tem, inclusive o que não cabe nela: as
                // colunas da grade existem na memória mesmo fora da área
                // visível, e cada quadro do Forms (Localizar Ativos, Ativos,
                // Navegador) vira um objeto com seus campos e grades.
                String json;
                try {
                    json = naEDT(LancadorForms::dadosJson, 30000);
                } catch (java.util.concurrent.TimeoutException te) {
                    responder("ERRO dados: a interface não respondeu em 30 s");
                    break;
                }
                responder("OK " + Base64.getEncoder().encodeToString(json.getBytes(StandardCharsets.UTF_8)));
                break;
            }
            case "grade": {
                // Lê a grade de resultados como tabela: os cabeçalhos do Forms
                // (FolderPrompt) dão o nome de cada coluna pelo x, e os campos
                // alinhados no mesmo x são os valores. Assim a leitura não
                // depende de nome de campo nem de contar TABs.
                String tsv;
                try {
                    tsv = naEDT(LancadorForms::grade, 20000);
                } catch (java.util.concurrent.TimeoutException te) {
                    responder("ERRO grade: a interface não respondeu em 20 s");
                    break;
                }
                responder("OK " + Base64.getEncoder().encodeToString(tsv.getBytes(StandardCharsets.UTF_8)));
                break;
            }
            case "arvore":
                // O mapa da tela: cada componente com classe, texto, posição e
                // foco. É a partir daqui que se monta o roteiro de teclas —
                // muito mais preciso do que interpretar uma captura de tela.
                String mapa;
                try {
                    mapa = naEDT(LancadorForms::arvore, 20000);
                } catch (java.util.concurrent.TimeoutException te) {
                    responder("ERRO arvore: a interface não respondeu em 20 s (Forms ocupado)");
                    break;
                }
                responder("OK " + Base64.getEncoder().encodeToString(mapa.getBytes(StandardCharsets.UTF_8)));
                break;
            case "foco":
                focar();
                java.awt.KeyboardFocusManager kfm = java.awt.KeyboardFocusManager.getCurrentKeyboardFocusManager();
                responder("OK janela=" + (kfm.getFocusedWindow() == null ? "nenhuma" : "sim")
                        + " dono=" + (kfm.getFocusOwner() == null ? "nenhum" : kfm.getFocusOwner().getClass().getSimpleName()));
                break;
            case "clique":
                // clique X Y — último recurso; preferimos teclado.
                String[] xy = arg.trim().split("\\s+");
                robo.mouseMove(Integer.parseInt(xy[0]), Integer.parseInt(xy[1]));
                robo.mousePress(java.awt.event.InputEvent.BUTTON1_DOWN_MASK);
                robo.mouseRelease(java.awt.event.InputEvent.BUTTON1_DOWN_MASK);
                responder("OK");
                break;
            default:
                responder("ERRO ordem desconhecida: " + cmd);
        }
        return true;
    }

    // Sem gerenciador de janelas (Xvfb), o X entrega o teclado à janela sob
    // o ponteiro e o Java só considera a janela "focada" depois de um clique
    // nela. Um clique no canto inferior direito (barra de status do Forms,
    // sem widget) resolve os dois de uma vez.
    private static void focar() {
        janela.validate();
        janela.toFront();
        if (!ENTRADA_JAVA) {
            // Xvfb sem gerenciador de janelas: só um clique dá o foco X.
            Dimension tela = Toolkit.getDefaultToolkit().getScreenSize();
            int x = Math.min(janela.getWidth(), tela.width) - 4;
            int y = Math.min(janela.getHeight(), tela.height) - 4;
            robo.mouseMove(x, y);
            robo.mousePress(java.awt.event.InputEvent.BUTTON1_DOWN_MASK);
            robo.mouseRelease(java.awt.event.InputEvent.BUTTON1_DOWN_MASK);
        }
        sincronizar();
        try {
            naEDT(() -> {
                janela.requestFocus();
                applet.requestFocusInWindow();
                // Se o foco parou no próprio applet (um Panel), desce para o
                // primeiro componente que aceita teclado — senão o TAB não vai
                // a lugar nenhum.
                java.awt.KeyboardFocusManager kfm = java.awt.KeyboardFocusManager.getCurrentKeyboardFocusManager();
                if (kfm.getFocusOwner() == applet || kfm.getFocusOwner() == null) {
                    java.awt.Component primeiro = primeiroFocavel(applet);
                    if (primeiro != null) primeiro.requestFocusInWindow();
                }
                return null;
            }, 5000);
        } catch (Exception e) { /* foco é melhor esforço */ }
        sincronizar();
    }

    // Robot.waitForIdle() (realSync) pode travar para sempre no Xvfb sem
    // gerenciador de janelas. Esperar a fila de eventos do Java esvaziar e
    // dar um respiro ao X é o bastante para o que fazemos aqui.
    // Tudo que toca a interface roda na thread de eventos do AWT (senão
    // disputa o tree lock com o Forms e trava os dois) e SEMPRE com prazo:
    // se o Forms estiver ocupado, o robô responde erro em vez de ficar preso.
    private static <T> T naEDT(java.util.concurrent.Callable<T> tarefa, long ms) throws Exception {
        if (java.awt.EventQueue.isDispatchThread()) return tarefa.call();
        java.util.concurrent.FutureTask<T> tf = new java.util.concurrent.FutureTask<>(tarefa);
        java.awt.EventQueue.invokeLater(tf);
        return tf.get(ms, java.util.concurrent.TimeUnit.MILLISECONDS);
    }

    private static void sincronizar() {
        try {
            naEDT(() -> null, 5000);
        } catch (Exception e) { /* fila ocupada: seguimos, o passo seguinte tem prazo próprio */ }
        robo.delay(60);
    }

    private static java.awt.Component primeiroFocavel(java.awt.Container c) {
        for (java.awt.Component f : c.getComponents()) {
            if (!f.isShowing()) continue;
            if (f instanceof java.awt.Container) {
                java.awt.Component achado = primeiroFocavel((java.awt.Container) f);
                if (achado != null) return achado;
            }
            if (f.isFocusable() && !(f instanceof java.awt.Container && ((java.awt.Container) f).getComponentCount() > 0)
                    && !(f instanceof java.awt.Label) && !(f instanceof javax.swing.JLabel)) return f;
        }
        return null;
    }

    private static void tecla(String combo) throws Exception {
        if (ENTRADA_JAVA) { teclaJava(combo); return; }
        List<Integer> mods = new ArrayList<>();
        int principal = -1;
        for (String parte : combo.toUpperCase().split("\\+")) {
            switch (parte) {
                case "CTRL": mods.add(KeyEvent.VK_CONTROL); break;
                case "SHIFT": mods.add(KeyEvent.VK_SHIFT); break;
                case "ALT": mods.add(KeyEvent.VK_ALT); break;
                default: principal = codigoTecla(parte);
            }
        }
        if (principal < 0) throw new IllegalArgumentException("tecla inválida: " + combo);
        for (int m : mods) robo.keyPress(m);
        robo.keyPress(principal);
        robo.keyRelease(principal);
        for (int i = mods.size() - 1; i >= 0; i--) robo.keyRelease(mods.get(i));
        // Espera a fila de eventos só DEPOIS de soltar tudo: o combo inteiro
        // é entregue antes da próxima ordem, sem tecla segurada no meio.
        sincronizar();
        robo.delay(40);
    }

    // ── entrada pela fila de eventos do Java ────────────────────────────
    // Componente escolhido por "focarcampo". Numa tela virtual sem teclado
    // (Weston headless não tem seat) NENHUMA janela fica ativa, o AWT não tem
    // dono do foco e um evento postado na fila não chega a lugar nenhum. Por
    // isso guardamos o alvo e entregamos o evento direto a ele.
    private static volatile java.awt.Component alvoAtual = null;

    private static java.awt.Component alvoTeclado() {
        if (alvoAtual != null && alvoAtual.isShowing()) return alvoAtual;
        java.awt.Component c = java.awt.KeyboardFocusManager.getCurrentKeyboardFocusManager().getFocusOwner();
        if (c == null) c = java.awt.KeyboardFocusManager.getCurrentKeyboardFocusManager().getPermanentFocusOwner();
        return c != null ? c : applet;
    }

    /** Entrega o evento ao componente, sem depender do dono do foco. */
    private static void postar(java.awt.AWTEvent ev) {
        Object fonte = ev.getSource();
        if (fonte instanceof java.awt.Component) {
            java.awt.Component c = (java.awt.Component) fonte;
            try {
                naEDT(() -> { c.dispatchEvent(ev); return null; }, 8000);
                return;
            } catch (Exception e) { /* cai para a fila */ }
        }
        Toolkit.getDefaultToolkit().getSystemEventQueue().postEvent(ev);
    }

    private static void teclaJava(String combo) throws Exception {
        int mods = 0;
        int principal = -1;
        for (String parte : combo.toUpperCase().split("\\+")) {
            switch (parte) {
                case "CTRL": mods |= KeyEvent.CTRL_DOWN_MASK; break;
                case "SHIFT": mods |= KeyEvent.SHIFT_DOWN_MASK; break;
                case "ALT": mods |= KeyEvent.ALT_DOWN_MASK; break;
                default: principal = codigoTecla(parte);
            }
        }
        if (principal < 0) throw new IllegalArgumentException("tecla inválida: " + combo);
        final int code = principal, m = mods;
        java.awt.Component alvo = alvoTeclado();
        // TAB/Shift+TAB: se o componente deixa a travessia de foco para o AWT
        // (Swing), um evento sintético não a dispara — fazemos a troca direto.
        // O Forms desliga a travessia e trata TAB como "próximo item"; nesse
        // caso o evento segue normalmente para ele.
        if (code == KeyEvent.VK_TAB && alvo.getFocusTraversalKeysEnabled()) {
            java.awt.AWTKeyStroke ks = java.awt.AWTKeyStroke.getAWTKeyStroke(KeyEvent.VK_TAB, m);
            boolean frente = alvo.getFocusTraversalKeys(java.awt.KeyboardFocusManager.FORWARD_TRAVERSAL_KEYS).contains(ks);
            boolean tras = alvo.getFocusTraversalKeys(java.awt.KeyboardFocusManager.BACKWARD_TRAVERSAL_KEYS).contains(ks);
            if (frente || tras) {
                final java.awt.Component c = alvo;
                naEDT(() -> { if (frente) c.transferFocus(); else c.transferFocusBackward(); return null; }, 5000);
                // a troca de foco é assíncrona: espera a fila assentar antes da próxima ordem
                robo.delay(150);
                sincronizar();
                return;
            }
        }
        long t = System.currentTimeMillis();
        char ch = KeyEvent.CHAR_UNDEFINED;
        if (code >= KeyEvent.VK_A && code <= KeyEvent.VK_Z) {
            ch = (m & KeyEvent.CTRL_DOWN_MASK) != 0 ? (char) (code - KeyEvent.VK_A + 1)
               : (m & KeyEvent.SHIFT_DOWN_MASK) != 0 ? (char) code : (char) (code + 32);
        } else if (code >= KeyEvent.VK_0 && code <= KeyEvent.VK_9) {
            ch = (char) code;
        } else if (code == KeyEvent.VK_ENTER) ch = '\n';
        else if (code == KeyEvent.VK_TAB) ch = '\t';
        else if (code == KeyEvent.VK_SPACE) ch = ' ';
        else if (code == KeyEvent.VK_BACK_SPACE) ch = '\b';
        else if (code == KeyEvent.VK_ESCAPE) ch = 27;
        else if (code == KeyEvent.VK_DELETE) ch = 127;
        // modificadores primeiro, como um teclado de verdade
        if ((m & KeyEvent.CTRL_DOWN_MASK) != 0) postar(new KeyEvent(alvo, KeyEvent.KEY_PRESSED, t, KeyEvent.CTRL_DOWN_MASK, KeyEvent.VK_CONTROL, KeyEvent.CHAR_UNDEFINED));
        if ((m & KeyEvent.SHIFT_DOWN_MASK) != 0) postar(new KeyEvent(alvo, KeyEvent.KEY_PRESSED, t, m & (KeyEvent.CTRL_DOWN_MASK | KeyEvent.SHIFT_DOWN_MASK), KeyEvent.VK_SHIFT, KeyEvent.CHAR_UNDEFINED));
        if ((m & KeyEvent.ALT_DOWN_MASK) != 0) postar(new KeyEvent(alvo, KeyEvent.KEY_PRESSED, t, m, KeyEvent.VK_ALT, KeyEvent.CHAR_UNDEFINED));
        postar(new KeyEvent(alvo, KeyEvent.KEY_PRESSED, t, m, code, ch));
        if (ch != KeyEvent.CHAR_UNDEFINED && (m & KeyEvent.ALT_DOWN_MASK) == 0)
            postar(new KeyEvent(alvo, KeyEvent.KEY_TYPED, t, m, KeyEvent.VK_UNDEFINED, ch));
        postar(new KeyEvent(alvo, KeyEvent.KEY_RELEASED, t, m, code, ch));
        if ((m & KeyEvent.ALT_DOWN_MASK) != 0) postar(new KeyEvent(alvo, KeyEvent.KEY_RELEASED, t, 0, KeyEvent.VK_ALT, KeyEvent.CHAR_UNDEFINED));
        if ((m & KeyEvent.SHIFT_DOWN_MASK) != 0) postar(new KeyEvent(alvo, KeyEvent.KEY_RELEASED, t, 0, KeyEvent.VK_SHIFT, KeyEvent.CHAR_UNDEFINED));
        if ((m & KeyEvent.CTRL_DOWN_MASK) != 0) postar(new KeyEvent(alvo, KeyEvent.KEY_RELEASED, t, 0, KeyEvent.VK_CONTROL, KeyEvent.CHAR_UNDEFINED));
        sincronizar();
    }

    private static void digitarCharJava(char c) throws Exception {
        java.awt.Component alvo = alvoTeclado();
        long t = System.currentTimeMillis();
        int code = KeyEvent.getExtendedKeyCodeForChar(c);
        int m = Character.isUpperCase(c) ? KeyEvent.SHIFT_DOWN_MASK : 0;
        postar(new KeyEvent(alvo, KeyEvent.KEY_PRESSED, t, m, code == KeyEvent.VK_UNDEFINED ? KeyEvent.VK_UNDEFINED : code, c));
        postar(new KeyEvent(alvo, KeyEvent.KEY_TYPED, t, m, KeyEvent.VK_UNDEFINED, c));
        postar(new KeyEvent(alvo, KeyEvent.KEY_RELEASED, t, m, code == KeyEvent.VK_UNDEFINED ? KeyEvent.VK_UNDEFINED : code, c));
    }

    // Lê o texto do componente focado sem passar pelo X: os campos do Forms
    // (oracle.forms.ui.VTextField / LWTextField) e do Swing têm getText().
    private static String lerCampoFocado() {
        try {
            return naEDT(() -> {
                java.awt.Component c = alvoTeclado();
                for (String metodo : new String[] {"getSelectedText", "getText"}) {
                    try {
                        java.lang.reflect.Method mt = c.getClass().getMethod(metodo);
                        Object v = mt.invoke(c);
                        if (v != null && !v.toString().isEmpty()) return v.toString();
                    } catch (Exception ignorada) { /* tenta o próximo */ }
                }
                return null;
            }, 5000);
        } catch (Exception e) {
            return null;  // sem resposta a tempo: quem chamou tenta pela área de transferência
        }
    }

    private static int codigoTecla(String nome) throws Exception {
        switch (nome) {
            case "ENTER": return KeyEvent.VK_ENTER;
            case "TAB": return KeyEvent.VK_TAB;
            case "ESC": case "ESCAPE": return KeyEvent.VK_ESCAPE;
            case "HOME": return KeyEvent.VK_HOME;
            case "END": return KeyEvent.VK_END;
            case "UP": case "CIMA": return KeyEvent.VK_UP;
            case "DOWN": case "BAIXO": return KeyEvent.VK_DOWN;
            case "LEFT": case "ESQ": return KeyEvent.VK_LEFT;
            case "RIGHT": case "DIR": return KeyEvent.VK_RIGHT;
            case "PGUP": return KeyEvent.VK_PAGE_UP;
            case "PGDN": return KeyEvent.VK_PAGE_DOWN;
            case "SPACE": case "ESPACO": return KeyEvent.VK_SPACE;
            case "BACKSPACE": return KeyEvent.VK_BACK_SPACE;
            case "DELETE": case "DEL": return KeyEvent.VK_DELETE;
            default:
                if (nome.length() == 1) {
                    char c = nome.charAt(0);
                    if (Character.isLetterOrDigit(c)) return KeyEvent.getExtendedKeyCodeForChar(c);
                }
                // F1..F12 e qualquer VK_ pelo nome (ex.: "MINUS")
                Field f = KeyEvent.class.getField("VK_" + nome);
                return f.getInt(null);
        }
    }

    private static void digitarChar(char c) throws Exception {
        boolean maiuscula = Character.isUpperCase(c);
        int code = KeyEvent.getExtendedKeyCodeForChar(Character.toLowerCase(c));
        if (code == KeyEvent.VK_UNDEFINED) { clipboard(String.valueOf(c)); tecla("CTRL+V"); return; }
        if (maiuscula) robo.keyPress(KeyEvent.VK_SHIFT);
        robo.keyPress(code);
        robo.keyRelease(code);
        if (maiuscula) robo.keyRelease(KeyEvent.VK_SHIFT);
    }

    /** Imagem da tela. No Xwayland headless o X não guarda o conteúdo (a captura
     *  sai preta), então desenhamos as próprias janelas Java; o Robot fica como
     *  reserva para ambientes onde a captura do X funciona. */
    private static BufferedImage capturar() throws Exception {
        Dimension tela = Toolkit.getDefaultToolkit().getScreenSize();
        if (!"robot".equalsIgnoreCase(System.getProperty("forms.captura", "java"))) {
            try {
                BufferedImage im = naEDT(() -> {
                    BufferedImage b = new BufferedImage(tela.width, tela.height, BufferedImage.TYPE_INT_RGB);
                    java.awt.Graphics2D g = b.createGraphics();
                    g.setColor(new java.awt.Color(0xD4D0C8));
                    g.fillRect(0, 0, tela.width, tela.height);
                    for (Window w : Window.getWindows()) {
                        if (!w.isShowing() || w.getWidth() <= 0) continue;
                        java.awt.Point p;
                        try {
                            p = w.getLocationOnScreen();
                        } catch (Exception semTela) {
                            p = w.getLocation();
                        }
                        g.translate(p.x, p.y);
                        w.printAll(g);
                        g.translate(-p.x, -p.y);
                    }
                    g.dispose();
                    return b;
                }, 15000);
                if (im != null) return im;
            } catch (Exception e) {
                responder("EVENTO captura-java-falhou " + e);
            }
        }
        return robo.createScreenCapture(new Rectangle(tela));
    }

    private static void clipboard(String s) {
        Clipboard c = Toolkit.getDefaultToolkit().getSystemClipboard();
        c.setContents(new StringSelection(s), null);
    }

    private static String lerClipboard() {
        try {
            Clipboard c = Toolkit.getDefaultToolkit().getSystemClipboard();
            Object o = c.getData(DataFlavor.stringFlavor);
            return o == null ? "" : o.toString();
        } catch (Exception e) {
            return "";
        }
    }

    /** Procura um botão/componente pelo rótulo visível (sem mnemônico). */
    private static java.awt.Component acharPorTexto(String rotulo) {
        String alvo = rotulo.replace("&", "").trim().toLowerCase();
        for (Window w : Window.getWindows()) {
            if (!w.isShowing()) continue;
            for (java.awt.Component f : todosOsComponentes(w, new ArrayList<>())) {
                if (!f.isShowing() || !f.isEnabled()) continue;
                String t = textoDe(f);
                if (t == null) continue;
                if (t.replace("&", "").trim().toLowerCase().equals(alvo)) return f;
            }
        }
        return null;
    }

    /** Aviso na tela: título, texto e botões. Vazio quando não há nenhum. */
    private static String dialogoJson() {
        for (Window w : Window.getWindows()) {
            if (!w.isShowing()) continue;
            List<java.awt.Container> quadros = new ArrayList<>();
            acharQuadros(w, quadros);
            for (java.awt.Container q : quadros) {
                String titulo = tituloDoQuadro(q);
                // Um aviso do Forms é pequeno e tem botão de confirmação.
                boolean temBotao = false;
                StringBuilder texto = new StringBuilder();
                List<String> botoes = new ArrayList<>();
                for (java.awt.Component f : todosOsComponentes(q, new ArrayList<>())) {
                    String t = textoDe(f);
                    if (t == null || t.isBlank()) continue;
                    String nomeClasse = f.getClass().getName();
                    if (nomeClasse.contains("Button")) { botoes.add(t.trim()); temBotao = true; }
                    else if (nomeClasse.contains("Label") || nomeClasse.contains("TextArea")) {
                        if (!t.trim().equals(titulo)) {
                            if (texto.length() > 0) texto.append(' ');
                            texto.append(t.trim());
                        }
                    }
                }
                boolean pequeno = q.getWidth() > 0 && q.getWidth() < 700 && q.getHeight() < 400;
                if (temBotao && pequeno && texto.length() > 0) {
                    StringBuilder j = new StringBuilder("{\"titulo\":").append(jsonTexto(titulo))
                        .append(",\"texto\":").append(jsonTexto(texto.toString()))
                        .append(",\"botoes\":[");
                    for (int i = 0; i < botoes.size(); i++) {
                        if (i > 0) j.append(',');
                        j.append(jsonTexto(botoes.get(i)));
                    }
                    return j.append("]}").toString();
                }
            }
        }
        return "{}";
    }

    private static boolean condicaoAtendida(String cond) throws Exception {
        final String c = cond.trim();
        return Boolean.TRUE.equals(naEDT(() -> {
            if (c.startsWith("campo:")) {
                String nome = c.substring(6).trim();
                for (Window w : Window.getWindows())
                    if (w.isShowing() && procurar(w, nome) != null) return true;
                return false;
            }
            if (c.startsWith("janela:")) {
                String titulo = c.substring(7).trim().toLowerCase();
                List<java.awt.Container> quadros = new ArrayList<>();
                for (Window w : Window.getWindows()) {
                    if (!w.isShowing()) continue;
                    acharQuadros(w, quadros);
                    if (tituloDe(w) != null && tituloDe(w).toLowerCase().contains(titulo)) return true;
                }
                for (java.awt.Container q : quadros)
                    if (tituloDoQuadro(q).toLowerCase().contains(titulo)) return true;
                return false;
            }
            if (c.equals("grade")) {
                for (Window w : Window.getWindows()) {
                    if (!w.isShowing()) continue;
                    for (java.awt.Container painel : paineisComCabecalho(w, new ArrayList<>()))
                        for (java.awt.Component f : painel.getComponents()) {
                            if (!ehCampo(f) || f.getY() <= 0) continue;
                            String t = textoDe(f);
                            if (t != null && !t.isBlank()) return true;
                        }
                }
                return false;
            }
            return false;
        }, 8000));
    }

    // ── tudo da tela em JSON ────────────────────────────────────────────
    private static String jsonTexto(String v) {
        if (v == null) return "null";
        StringBuilder b = new StringBuilder("\"");
        for (char c : v.toCharArray()) {
            switch (c) {
                case '"': b.append("\\\""); break;
                case '\\': b.append("\\\\"); break;
                case '\n': b.append("\\n"); break;
                case '\r': b.append("\\r"); break;
                case '\t': b.append("\\t"); break;
                default:
                    if (c < 0x20) b.append(String.format("\\u%04x", (int) c));
                    else b.append(c);
            }
        }
        return b.append('"').toString();
    }

    /** Quadros do Forms (janelas internas), cada um com seus campos e grades. */
    private static String dadosJson() {
        StringBuilder j = new StringBuilder("{\"quadros\":[");
        boolean primeiro = true;
        List<java.awt.Container> quadros = new ArrayList<>();
        for (Window w : Window.getWindows()) {
            if (!w.isShowing()) continue;
            acharQuadros(w, quadros);
            if (quadros.isEmpty()) quadros.add(w);  // sem quadros internos: a própria janela
        }
        for (java.awt.Container q : quadros) {
            StringBuilder bloco = new StringBuilder();
            bloco.append("{\"titulo\":").append(jsonTexto(tituloDoQuadro(q)));
            bloco.append(",\"campos\":[");
            // o quadro é montado à parte e só entra no JSON se tiver conteúdo
            boolean p1 = true;
            for (java.awt.Component f : todosOsComponentes(q, new ArrayList<>())) {
                if (f.getClass().getName().contains("FolderPrompt")) continue;
                if (!ehCampo(f)) continue;
                String v = textoDe(f);
                if (v == null || v.isBlank()) continue;   // campo vazio não é dado
                String nome = f.getName();
                if (!p1) bloco.append(',');
                p1 = false;
                bloco.append("{\"nome\":").append(jsonTexto(nome))
                     .append(",\"valor\":").append(jsonTexto(v.trim()))
                     .append(",\"x\":").append(f.getX()).append(",\"y\":").append(f.getY()).append('}');
            }
            String grades = gradesJson(q);
            if (p1 && grades.isEmpty()) continue;   // quadro sem nada a dizer
            bloco.append("],\"grades\":[").append(grades).append("]}");
            if (!primeiro) j.append(',');
            primeiro = false;
            j.append(bloco);
        }
        j.append("],\"rodape\":").append(jsonTexto(rodape())).append('}');
        return j.toString();
    }

    private static boolean ehCampo(java.awt.Component f) {
        String c = f.getClass().getName();
        return c.contains("VTextField") || c.contains("VPopList") || c.contains("TextField")
            || c.contains("Checkbox") || c.contains("CheckBox") || c.contains("ComboBox");
    }

    private static List<java.awt.Component> todosOsComponentes(java.awt.Container c, List<java.awt.Component> saida) {
        for (java.awt.Component f : c.getComponents()) {
            saida.add(f);
            if (f instanceof java.awt.Container) todosOsComponentes((java.awt.Container) f, saida);
        }
        return saida;
    }

    /** Quadro = janela interna do Forms (ExtendedFrame). */
    private static void acharQuadros(java.awt.Container c, List<java.awt.Container> saida) {
        for (java.awt.Component f : c.getComponents()) {
            if (f.getClass().getName().contains("ExtendedFrame") && f.isShowing()) saida.add((java.awt.Container) f);
            else if (f instanceof java.awt.Container) acharQuadros((java.awt.Container) f, saida);
        }
    }

    private static String tituloDoQuadro(java.awt.Container q) {
        for (java.awt.Component f : todosOsComponentes(q, new ArrayList<>())) {
            if (f.getClass().getName().contains("LWLabel")) {
                String t = textoDe(f);
                if (t != null && !t.isBlank()) return t.trim();
            }
        }
        String t = q instanceof Window ? tituloDe((Window) q) : q.getName();
        return t == null ? "" : t;
    }

    /** A barra de status do Forms: "Registro: 1/1", mensagens, modo. */
    private static String rodape() {
        StringBuilder sb = new StringBuilder();
        for (Window w : Window.getWindows()) {
            if (!w.isShowing()) continue;
            for (java.awt.Component f : todosOsComponentes(w, new ArrayList<>())) {
                if (f.getY() < w.getHeight() * 0.85) continue;   // só a faixa do rodapé
                if (ehCampo(f)) continue;                        // campo não é mensagem
                String t = textoDe(f);
                if (t != null && !t.isBlank() && t.length() < 120) {
                    if (sb.length() > 0) sb.append(" | ");
                    sb.append(t.trim());
                }
            }
        }
        return sb.toString();
    }

    private static String gradesJson(java.awt.Container raiz) {
        StringBuilder j = new StringBuilder();
        boolean primeira = true;
        for (java.awt.Container painel : paineisComCabecalho(raiz, new ArrayList<>())) {
            java.util.TreeMap<Integer, String> colunas = new java.util.TreeMap<>();
            for (java.awt.Component f : painel.getComponents())
                if (f.getClass().getName().contains("FolderPrompt")) {
                    String t = textoDe(f);
                    if (t != null && !t.isBlank()) colunas.put(f.getX(), t.trim());
                }
            if (colunas.size() < 2) continue;
            java.util.TreeMap<Integer, java.util.TreeMap<Integer, String>> linhas = new java.util.TreeMap<>();
            for (java.awt.Component f : painel.getComponents()) {
                if (f.getClass().getName().contains("FolderPrompt") || f.getY() <= 0) continue;
                if (!ehCampo(f)) continue;   // rótulo e legenda não são linha de dados
                String t = textoDe(f);
                if (t == null || t.isBlank()) continue;
                Integer col = colunas.floorKey(f.getX() + 5);
                if (col == null) col = colunas.firstKey();
                linhas.computeIfAbsent(f.getY(), k -> new java.util.TreeMap<>()).put(col, t.trim());
            }
            if (linhas.isEmpty()) continue;
            if (!primeira) j.append(',');
            primeira = false;
            j.append("{\"colunas\":[");
            boolean p = true;
            for (String c : colunas.values()) { if (!p) j.append(','); p = false; j.append(jsonTexto(c)); }
            j.append("],\"linhas\":[");
            boolean pl = true;
            for (java.util.TreeMap<Integer, String> linha : linhas.values()) {
                if (!pl) j.append(',');
                pl = false;
                j.append('{');
                boolean pc = true;
                for (Map.Entry<Integer, String> col : colunas.entrySet()) {
                    String valor = linha.get(col.getKey());
                    if (valor == null || valor.isBlank()) continue;   // coluna vazia sai do JSON
                    if (!pc) j.append(',');
                    pc = false;
                    j.append(jsonTexto(col.getValue())).append(':').append(jsonTexto(valor));
                }
                j.append('}');
            }
            j.append("]}");
        }
        return j.toString();
    }

    /** Grade de resultados em TSV: primeira linha os cabeçalhos, depois as linhas com valor. */
    private static String grade() {
        StringBuilder saida = new StringBuilder();
        for (Window w : Window.getWindows()) {
            if (!w.isShowing()) continue;
            for (java.awt.Container painel : paineisComCabecalho(w, new ArrayList<>())) {
                java.util.TreeMap<Integer, String> colunas = new java.util.TreeMap<>();
                for (java.awt.Component f : painel.getComponents())
                    if (f.getClass().getName().contains("FolderPrompt")) {
                        String t = textoDe(f);
                        if (t != null && !t.isBlank()) colunas.put(f.getX(), t.trim());
                    }
                if (colunas.size() < 2) continue;
                // valores por linha (y) e coluna (x mais próximo de um cabeçalho)
                java.util.TreeMap<Integer, java.util.TreeMap<Integer, String>> linhas = new java.util.TreeMap<>();
                for (java.awt.Component f : painel.getComponents()) {
                    if (f.getClass().getName().contains("FolderPrompt")) continue;
                    if (f.getY() <= 0 || !ehCampo(f)) continue;
                    String t = textoDe(f);
                    if (t == null || t.isBlank()) continue;
                    Integer col = colunas.floorKey(f.getX() + 5);
                    if (col == null) col = colunas.firstKey();
                    linhas.computeIfAbsent(f.getY(), k -> new java.util.TreeMap<>()).put(col, t.trim());
                }
                if (linhas.isEmpty()) continue;
                saida.append(String.join("\t", colunas.values())).append('\n');
                for (java.util.TreeMap<Integer, String> linha : linhas.values()) {
                    StringBuilder l = new StringBuilder();
                    for (Integer x : colunas.keySet()) {
                        if (l.length() > 0) l.append('\t');
                        l.append(linha.getOrDefault(x, ""));
                    }
                    saida.append(l).append('\n');
                }
                saida.append('\n');
            }
        }
        return saida.length() == 0 ? "(nenhuma grade com valores na tela)" : saida.toString();
    }

    private static List<java.awt.Container> paineisComCabecalho(java.awt.Container c, List<java.awt.Container> achados) {
        boolean temCabecalho = false;
        for (java.awt.Component f : c.getComponents()) {
            if (f.getClass().getName().contains("FolderPrompt")) temCabecalho = true;
            if (f instanceof java.awt.Container) paineisComCabecalho((java.awt.Container) f, achados);
        }
        if (temCabecalho) achados.add(c);
        return achados;
    }

    private static String donoDoFoco() throws Exception {
        return naEDT(() -> {
            java.awt.Component c = java.awt.KeyboardFocusManager.getCurrentKeyboardFocusManager().getFocusOwner();
            if (c == null) return "(nenhum)";
            String n = c.getName();
            return (n == null || n.isEmpty()) ? c.getClass().getSimpleName() : n;
        }, 5000);
    }

    /** Clique de mouse pela fila de eventos do Java (sem XTEST: o Xwayland
     *  headless não tem seat e aborta ao receber input do X). */
    private static void cliqueSintetico(java.awt.Component a) {
        long t = System.currentTimeMillis();
        int x = Math.max(1, a.getWidth() / 2), y = Math.max(1, a.getHeight() / 2);
        a.dispatchEvent(new java.awt.event.MouseEvent(a, java.awt.event.MouseEvent.MOUSE_PRESSED, t,
                java.awt.event.InputEvent.BUTTON1_DOWN_MASK, x, y, 1, false));
        a.dispatchEvent(new java.awt.event.MouseEvent(a, java.awt.event.MouseEvent.MOUSE_RELEASED, t + 1,
                java.awt.event.InputEvent.BUTTON1_DOWN_MASK, x, y, 1, false));
        a.dispatchEvent(new java.awt.event.MouseEvent(a, java.awt.event.MouseEvent.MOUSE_CLICKED, t + 2,
                java.awt.event.InputEvent.BUTTON1_DOWN_MASK, x, y, 1, false));
    }

    private static java.awt.Component acharPorNome(String nome) throws Exception {
        return naEDT(() -> {
            for (Window w : Window.getWindows()) {
                if (!w.isShowing()) continue;
                java.awt.Component c = procurar(w, nome);
                if (c != null) return c;
            }
            return null;
        }, 8000);
    }

    private static java.awt.Component procurar(java.awt.Container c, String nome) {
        for (java.awt.Component f : c.getComponents()) {
            if (nome.equals(f.getName())) return f;
            if (f instanceof java.awt.Container) {
                java.awt.Component achado = procurar((java.awt.Container) f, nome);
                if (achado != null) return achado;
            }
        }
        return null;
    }

    private static String arvore() {
        StringBuilder sb = new StringBuilder();
        java.awt.Component focado = java.awt.KeyboardFocusManager.getCurrentKeyboardFocusManager().getFocusOwner();
        int[] contados = {0};
        for (Window w : Window.getWindows()) {
            if (!w.isShowing()) continue;
            sb.append("JANELA ").append(w.getClass().getSimpleName()).append(" \"").append(tituloDe(w)).append("\" ")
              .append(w.getWidth()).append('x').append(w.getHeight()).append('\n');
            descrever(w, 1, sb, focado, contados);
        }
        if (contados[0] >= LIMITE_ARVORE) sb.append("... (lista truncada em ").append(LIMITE_ARVORE).append(" componentes)\n");
        return sb.toString();
    }

    private static final int LIMITE_ARVORE = 500;

    private static void descrever(java.awt.Container c, int nivel, StringBuilder sb,
                                  java.awt.Component focado, int[] contados) {
        for (java.awt.Component f : c.getComponents()) {
            if (contados[0]++ >= LIMITE_ARVORE) return;
            if (!f.isVisible()) continue;
            sb.append("  ".repeat(nivel)).append(f == focado ? "> " : "  ")
              .append(f.getClass().getName());
            String nome = f.getName();
            if (nome != null && !nome.isEmpty() && !nome.startsWith("null")) sb.append(" nome=").append(nome);
            String texto = textoDe(f);
            if (texto != null && !texto.isEmpty()) sb.append(" texto=\"").append(texto.replace('\n', ' ')).append('"');
            java.awt.Rectangle r = f.getBounds();
            sb.append(" em ").append(r.x).append(',').append(r.y)
              .append(' ').append(r.width).append('x').append(r.height);
            if (!f.isEnabled()) sb.append(" desabilitado");
            if (f.isFocusable()) sb.append(" focavel");
            sb.append('\n');
            if (f instanceof java.awt.Container) descrever((java.awt.Container) f, nivel + 1, sb, focado, contados);
        }
    }

    private static String textoDe(java.awt.Component f) {
        // Só leituras baratas: getValue em componentes do Forms pode disparar
        // comunicação com o servidor no meio do mapeamento.
        for (String metodo : new String[] {"getText", "getLabel"}) {
            try {
                Object v = f.getClass().getMethod(metodo).invoke(f);
                if (v != null) {
                    String t = v.toString();
                    if (!t.isEmpty()) return t.length() > 80 ? t.substring(0, 80) + "…" : t;
                }
            } catch (Exception ignorada) { /* componente sem esse método */ }
        }
        return null;
    }

    private static String tituloDe(Window w) {
        if (w instanceof Frame) return ((Frame) w).getTitle();
        if (w instanceof java.awt.Dialog) return ((java.awt.Dialog) w).getTitle();
        return "";
    }

    private static void responder(String s) {
        proto.println(s);
        proto.flush();
    }

    private static volatile boolean saidaAutorizada = false;
    private static volatile boolean pronto = false;

    // O applet do EBS e o cliente Forms chamam System.exit() por conta
    // própria (fim de sessão, janela fechada) e a JVM some sem rastro. Um
    // SecurityManager não serve: o Forms 10g, ao vê-lo, chama
    // checkTopLevelWindow, que o Java 21 não tem mais. O caminho que resta é
    // o shutdown hook: roda antes da morte, e nessa hora quem pediu a saída
    // ainda está parado dentro de Runtime.exit — o dump de threads o entrega.
    // De quebra, fotografa a tela (se o Forms mostrou um erro, está na foto)
    // e, com -Dforms.segurar.exit=true, não devolve: a JVM fica viva para o
    // roteiro terminar de olhar.
    private static void vigiarExit() {
        Runtime.getRuntime().addShutdownHook(new Thread(() -> {
            if (saidaAutorizada) return;
            String culpado = "?";
            StringBuilder sb = new StringBuilder();
            for (Map.Entry<Thread, StackTraceElement[]> e : Thread.getAllStackTraces().entrySet()) {
                StackTraceElement[] st = e.getValue();
                boolean saindo = false;
                for (StackTraceElement el : st)
                    if (el.getClassName().equals("java.lang.Shutdown") || el.getClassName().equals("java.lang.Runtime") && el.getMethodName().equals("exit"))
                        saindo = true;
                if (!saindo) continue;
                culpado = e.getKey().getName();
                sb.append("\n  thread '").append(culpado).append("':");
                for (StackTraceElement el : st) sb.append("\n    at ").append(el);
            }
            System.err.println("[LancadorForms] JVM encerrando por System.exit — pedido por: " + culpado + sb);
            StringBuilder jan = new StringBuilder();
            for (Window w : Window.getWindows()) jan.append(" [").append(w.getClass().getSimpleName()).append(':').append(tituloDe(w)).append(w.isShowing() ? "" : " oculta").append(']');
            System.err.println("[LancadorForms] janelas no momento:" + jan);
            String foto = System.getProperty("forms.captura.saida", "");
            if (!foto.isEmpty() && robo != null) {
                try {
                    ImageIO.write(capturar(), "png", new File(foto));
                    System.err.println("[LancadorForms] captura no encerramento: " + foto);
                } catch (Throwable t) { System.err.println("[LancadorForms] sem captura no encerramento: " + t); }
            }
            responder("EVENTO exit-pedido thread=" + culpado);
            // Segurar a saída só faz sentido com o lançador operacional (para o
            // roteiro fotografar a tela antes de morrer) e nunca para sempre:
            // uma falha na PARTIDA precisa encerrar e devolver o erro.
            if (Boolean.getBoolean("forms.segurar.exit") && pronto) {
                System.err.println("[LancadorForms] segurando a saída por até 120 s (forms.segurar.exit=true)");
                long limite = System.currentTimeMillis() + 120_000;
                try {
                    while (!saidaAutorizada && System.currentTimeMillis() < limite) Thread.sleep(500);
                } catch (InterruptedException ie) { /* liberado */ }
            }
        }, "vigia-exit"));
    }

    private static void encerrar() {
        saidaAutorizada = true;
        try { if (applet != null) { applet.stop(); applet.destroy(); } } catch (Throwable t) { /* encerrando */ }
        try { if (janela != null) janela.dispose(); } catch (Throwable t) { /* encerrando */ }
        System.exit(0);
    }

    // ── jnlp ────────────────────────────────────────────────────────────
    private static List<String> lerJnlp(Path arquivo) throws Exception {
        DocumentBuilderFactory dbf = DocumentBuilderFactory.newInstance();
        dbf.setNamespaceAware(false);
        Document doc = dbf.newDocumentBuilder().parse(arquivo.toFile());
        Element raiz = doc.getDocumentElement();
        codebase = raiz.getAttribute("codebase");
        if (!codebase.endsWith("/")) codebase += "/";
        List<String> jars = new ArrayList<>();
        NodeList nj = doc.getElementsByTagName("jar");
        for (int i = 0; i < nj.getLength(); i++) {
            String href = ((Element) nj.item(i)).getAttribute("href");
            jars.add(href);
            int barra = href.lastIndexOf('/');
            if (barra > 0 && href.contains("/jar/")) dirJars = href.substring(0, barra + 1);
        }
        NodeList nd = doc.getElementsByTagName("applet-desc");
        if (nd.getLength() > 0) mainClass = ((Element) nd.item(0)).getAttribute("main-class");
        NodeList np = doc.getElementsByTagName("param");
        for (int i = 0; i < np.getLength(); i++) {
            Element e = (Element) np.item(i);
            params.put(e.getAttribute("name"), e.getAttribute("value"));
        }
        // O JWS injeta as <property> como propriedades de sistema.
        NodeList nprop = doc.getElementsByTagName("property");
        for (int i = 0; i < nprop.getLength(); i++) {
            Element e = (Element) nprop.item(i);
            System.setProperty(e.getAttribute("name"), e.getAttribute("value"));
        }
        return jars;
    }

    private static URL baixarJar(String href, Path cache) throws Exception {
        URL origem = href.startsWith("http") ? new URL(href) : new URL(new URL(codebase), href);
        String nome = href.substring(href.lastIndexOf('/') + 1);
        Path destino = cache.resolve(nome);
        if (!Files.exists(destino) || Files.size(destino) == 0) {
            try (InputStream in = origem.openStream()) {
                Files.copy(in, destino, StandardCopyOption.REPLACE_EXISTING);
            }
            responder("EVENTO baixado " + nome);
        }
        return destino.toUri().toURL();
    }

    // ── serviços do Java Web Start (javax.jnlp) ─────────────────────────
    private static void registrarServicosJnlp() {
        final javax.jnlp.BasicService basico = new javax.jnlp.BasicService() {
            public URL getCodeBase() { try { return new URL(codebase); } catch (Exception e) { return null; } }
            public boolean isOffline() { return false; }
            public boolean showDocument(URL url) { responder("EVENTO documento " + url); return true; }
            public boolean isWebBrowserSupported() { return true; }
        };
        // O FndFormsEngine do EBS se registra como instância única; cada JVM
        // nossa é uma instância só, então basta aceitar o registro.
        final javax.jnlp.SingleInstanceService unica = new javax.jnlp.SingleInstanceService() {
            public void addSingleInstanceListener(javax.jnlp.SingleInstanceListener l) { }
            public void removeSingleInstanceListener(javax.jnlp.SingleInstanceListener l) { }
        };
        javax.jnlp.ServiceManager.setServiceManagerStub(new javax.jnlp.ServiceManagerStub() {
            public Object lookup(String name) throws javax.jnlp.UnavailableServiceException {
                if ("javax.jnlp.BasicService".equals(name)) return basico;
                if ("javax.jnlp.SingleInstanceService".equals(name)) return unica;
                responder("EVENTO servico-jnlp-indisponivel " + name);
                throw new javax.jnlp.UnavailableServiceException(name);
            }
            public String[] getServiceNames() {
                return new String[] {"javax.jnlp.BasicService", "javax.jnlp.SingleInstanceService"};
            }
        });
    }

    // ── o "navegador" que o applet enxerga ──────────────────────────────
    static class Stub implements AppletStub, AppletContext {
        public boolean isActive() { return true; }
        public URL getDocumentBase() { try { return new URL(codebase); } catch (Exception e) { return null; } }
        public URL getCodeBase() { try { return new URL(codebase); } catch (Exception e) { return null; } }
        public String getParameter(String name) {
            // -Dforms.param.NOME=valor sobrepõe o que veio no jnlp (ajuste de
            // campo sem recompilar; chega por EBS_FORMS_PARAMS no environment).
            // O valor @nulo@ faz o parâmetro "não existir".
            for (String chave : System.getProperties().stringPropertyNames())
                if (chave.startsWith("forms.param.") && chave.substring(12).equalsIgnoreCase(name)) {
                    String v = System.getProperty(chave);
                    return "@nulo@".equals(v) ? null : v;
                }
            // O lançador do EBS (JNLPAppletContext.setHTTPCookie) só aceita
            // clientBrowser em Windows/macOS — em Linux a lista é vazia e ele
            // aborta. Sem o parâmetro ele pula a checagem e segue para o que
            // importa: recriar a sessão e guardar o cookie. O parâmetro só
            // serviria para abrir URLs no navegador, coisa que aqui é nossa.
            if ("clientBrowser".equalsIgnoreCase(name)) {
                String so = System.getProperty("os.name", "").toLowerCase();
                if (!so.startsWith("win") && !so.startsWith("mac")) return null;
            }
            for (Map.Entry<String, String> e : params.entrySet())
                if (e.getKey().equalsIgnoreCase(name)) return e.getValue();
            return null;
        }
        public AppletContext getAppletContext() { return this; }
        public void appletResize(int w, int h) { /* tamanho é nosso */ }

        public AudioClip getAudioClip(URL url) { return null; }
        public Image getImage(URL url) { return Toolkit.getDefaultToolkit().getImage(url); }
        public Applet getApplet(String name) { return null; }
        public Enumeration<Applet> getApplets() { return java.util.Collections.emptyEnumeration(); }
        // Exportar/anexos: o Forms pede ao navegador que abra uma URL. Não
        // temos navegador; informamos e o lado Python decide o que baixar.
        public void showDocument(URL url) { responder("EVENTO documento " + url); }
        public void showDocument(URL url, String target) { responder("EVENTO documento " + url); }
        public void showStatus(String status) { if (status != null && !status.isEmpty()) responder("EVENTO status " + status.replace('\n', ' ')); }
        public void setStream(String key, InputStream stream) { }
        public InputStream getStream(String key) { return null; }
        public Iterator<String> getStreamKeys() { return java.util.Collections.emptyIterator(); }
    }
}
