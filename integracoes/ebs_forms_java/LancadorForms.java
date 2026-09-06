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
    private static int larg = 1280, alt = 900;

    public static void main(String[] args) throws Exception {
        if (args.length < 2) {
            System.err.println("uso: LancadorForms <arquivo.jnlp> <pasta-cache-jars> [largura] [altura]");
            System.exit(2);
        }
        proto = new PrintStream(new java.io.FileOutputStream(java.io.FileDescriptor.out), true, "UTF-8");
        // Tudo que o Forms imprimir vai para stderr; o stdout fica só para o protocolo.
        System.setOut(System.err);

        Path jnlp = Paths.get(args[0]);
        Path cache = Paths.get(args[1]);
        if (args.length >= 4) { larg = Integer.parseInt(args[2]); alt = Integer.parseInt(args[3]); }
        Files.createDirectories(cache);

        List<String> jars = lerJnlp(jnlp);
        List<URL> urls = new ArrayList<>();
        for (String j : jars) urls.add(baixarJar(j, cache));
        responder("EVENTO jars " + urls.size());

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
        janela.setLayout(new BorderLayout());
        janela.add(applet, BorderLayout.CENTER);
        janela.setSize(larg, alt);
        janela.setLocation(0, 0);
        applet.setPreferredSize(new Dimension(larg, alt));
        janela.setVisible(true);
        applet.init();
        applet.start();
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
        robo = new Robot();
        // Sem esperar a fila de eventos entre pressionar e soltar: com o
        // auto-repeat do X, um Ctrl+V "segurado" colava dezenas de vezes.
        robo.setAutoDelay(30);
        robo.setAutoWaitForIdle(false);
        focar();
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
                clipboard(txt);
                tecla("CTRL+V");
                responder("OK");
                break;
            case "digitar":
                // Só ASCII, tecla a tecla — para números e códigos, é mais
                // fiel ao que uma pessoa faz e dispara as validações do campo.
                String s = new String(Base64.getDecoder().decode(arg.trim()), StandardCharsets.UTF_8);
                for (char c : s.toCharArray()) digitarChar(c);
                responder("OK");
                break;
            case "copiar":
                // Seleciona o campo atual e devolve o conteúdo em base64.
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
            case "foto":
                Rectangle r = new Rectangle(Toolkit.getDefaultToolkit().getScreenSize());
                BufferedImage img = robo.createScreenCapture(r);
                File f = new File(arg.trim());
                if (f.getParentFile() != null) f.getParentFile().mkdirs();
                ImageIO.write(img, "png", f);
                responder("OK " + f.getAbsolutePath());
                break;
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
        Dimension tela = Toolkit.getDefaultToolkit().getScreenSize();
        int x = Math.min(janela.getWidth(), tela.width) - 4;
        int y = Math.min(janela.getHeight(), tela.height) - 4;
        robo.mouseMove(x, y);
        robo.mousePress(java.awt.event.InputEvent.BUTTON1_DOWN_MASK);
        robo.mouseRelease(java.awt.event.InputEvent.BUTTON1_DOWN_MASK);
        sincronizar();
        try {
            java.awt.EventQueue.invokeAndWait(() -> {
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
            });
        } catch (Exception e) { /* foco é melhor esforço */ }
        sincronizar();
    }

    // Robot.waitForIdle() (realSync) pode travar para sempre no Xvfb sem
    // gerenciador de janelas. Esperar a fila de eventos do Java esvaziar e
    // dar um respiro ao X é o bastante para o que fazemos aqui.
    private static void sincronizar() {
        try {
            java.awt.EventQueue.invokeAndWait(() -> { });
        } catch (Exception e) { /* só sincronização */ }
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

    private static String tituloDe(Window w) {
        if (w instanceof Frame) return ((Frame) w).getTitle();
        if (w instanceof java.awt.Dialog) return ((java.awt.Dialog) w).getTitle();
        return "";
    }

    private static void responder(String s) {
        proto.println(s);
        proto.flush();
    }

    private static void encerrar() {
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
        for (int i = 0; i < nj.getLength(); i++) jars.add(((Element) nj.item(i)).getAttribute("href"));
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
        javax.jnlp.ServiceManager.setServiceManagerStub(new javax.jnlp.ServiceManagerStub() {
            public Object lookup(String name) throws javax.jnlp.UnavailableServiceException {
                if ("javax.jnlp.BasicService".equals(name)) return basico;
                responder("EVENTO servico-jnlp-indisponivel " + name);
                throw new javax.jnlp.UnavailableServiceException(name);
            }
            public String[] getServiceNames() { return new String[] {"javax.jnlp.BasicService"}; }
        });
    }

    // ── o "navegador" que o applet enxerga ──────────────────────────────
    static class Stub implements AppletStub, AppletContext {
        public boolean isActive() { return true; }
        public URL getDocumentBase() { try { return new URL(codebase); } catch (Exception e) { return null; } }
        public URL getCodeBase() { try { return new URL(codebase); } catch (Exception e) { return null; } }
        public String getParameter(String name) {
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
