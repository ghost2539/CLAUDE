package javax.jnlp;

import java.io.File;
import java.io.IOException;

/** Cópia mínima da API do Java Web Start, que o OpenJDK 21 não traz mais. */
public interface ExtendedService {
    FileContents openFile(File file) throws IOException;
    FileContents[] openFiles(File[] files) throws IOException;
}
