package javax.jnlp;

import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;

/** Cópia mínima da API do Java Web Start, que o OpenJDK 21 não traz mais. */
public interface FileContents {
    String getName() throws IOException;
    InputStream getInputStream() throws IOException;
    OutputStream getOutputStream(boolean overwrite) throws IOException;
    long getLength() throws IOException;
    boolean canRead() throws IOException;
    boolean canWrite() throws IOException;
    long getMaxLength() throws IOException;
    long setMaxLength(long maxlength) throws IOException;
}
