package javax.jnlp;

import java.net.URL;

/** Cópia mínima da API do Java Web Start, que o OpenJDK 21 não traz mais. */
public interface BasicService {
    URL getCodeBase();
    boolean isOffline();
    boolean showDocument(URL url);
    boolean isWebBrowserSupported();
}
