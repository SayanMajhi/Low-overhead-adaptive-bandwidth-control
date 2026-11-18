import java.io.BufferedOutputStream;
import java.util.SplittableRandom;

/** Emit one unsigned byte per sampled pair index in replicate-major order. */
public class BootstrapIndices {
    public static void main(String[] args) throws Exception {
        int pairs = Integer.parseInt(args[0]);
        if (pairs != 10 && pairs != 30) {
            throw new IllegalArgumentException("Expected 10 tuning or 30 held-out pairs");
        }
        if (Runtime.version().feature() != 21) {
            throw new IllegalArgumentException("Expected Java 21");
        }

        var random = new SplittableRandom(2026111401L);
        try (var output = new BufferedOutputStream(System.out)) {
            for (int replicate = 0; replicate < 100_000; replicate++) {
                for (int draw = 0; draw < pairs; draw++) {
                    output.write(random.nextInt(pairs));
                }
            }
        }
    }
}
