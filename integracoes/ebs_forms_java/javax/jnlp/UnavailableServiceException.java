package javax.jnlp;

/** Cópia mínima da API do Java Web Start, que o OpenJDK 21 não traz mais. */
public class UnavailableServiceException extends Exception {
    public UnavailableServiceException() { super(); }
    public UnavailableServiceException(String msg) { super(msg); }
}
