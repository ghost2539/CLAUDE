package javax.jnlp;

/**
 * Cópia mínima da API do Java Web Start, que o OpenJDK 21 não traz mais.
 *
 * O cliente Forms do EBS (FndFormsEngine) pede ao Web Start o BasicService
 * — para abrir URLs no navegador (Exportar, anexos). O LancadorForms
 * registra aqui um stub que devolve a nossa implementação; qualquer outro
 * serviço é declarado indisponível, que é o comportamento previsto pela API.
 */
public final class ServiceManager {
    private static ServiceManagerStub stub;

    private ServiceManager() { }

    public static synchronized void setServiceManagerStub(ServiceManagerStub s) {
        if (stub == null) stub = s;
    }

    public static Object lookup(String name) throws UnavailableServiceException {
        ServiceManagerStub s = stub;
        if (s == null) throw new UnavailableServiceException(name);
        return s.lookup(name);
    }

    public static String[] getServiceNames() {
        ServiceManagerStub s = stub;
        return s == null ? new String[0] : s.getServiceNames();
    }
}
