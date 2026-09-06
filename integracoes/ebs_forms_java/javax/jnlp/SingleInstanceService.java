package javax.jnlp;

/** Cópia mínima da API do Java Web Start, que o OpenJDK 21 não traz mais. */
public interface SingleInstanceService {
    void addSingleInstanceListener(SingleInstanceListener listener);
    void removeSingleInstanceListener(SingleInstanceListener listener);
}
