package app;

import java.io.IOException;

public class App {
    public void extract(String file) throws IOException {
        Runtime.getRuntime().exec("tar -xf " + file);
    }

    public void extractSafely(String file) throws IOException {
        new ProcessBuilder("tar", "-xf", file).start();
    }
}
