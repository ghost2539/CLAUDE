package javax.jnlp;

/** Cópia mínima da API do Java Web Start, que o OpenJDK 21 não traz mais. */
public interface ServiceManagerStub {
    Object lookup(String name) throws UnavailableServiceException;
    String[] getServiceNames();
}
