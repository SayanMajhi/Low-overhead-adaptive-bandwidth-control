import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Arrays;
import java.util.SplittableRandom;

/** Gate-statistic implementation independent of the experiment runner. */
class GateBootstrap {
    static final int N = 30;
    static final int REPS = 100_000;
    static final String[] NAMES = {"feedback_savings_pct", "completion_loss_pp",
        "goodput_retention", "p95_increase_ms", "settle_increase_ms"};

    record Pair(long pBytes, long pFrames, long pGoodput, Long pDelay,
                long aBytes, long aFrames, long aGoodput, Long aDelay,
                long pSettle, long aSettle, String pState, String aState) {}

    static double[] metrics(Pair[] draws) {
        long pBytes = 0;
        long aBytes = 0;
        long pGood = 0;
        long aGood = 0;
        long frameGap = 0;
        long delayGap = 0;
        long settleGap = 0;
        boolean missingDelay = false;
        for (Pair draw : draws) {
            pBytes += draw.pBytes();
            aBytes += draw.aBytes();
            pGood += draw.pGoodput();
            aGood += draw.aGoodput();
            frameGap += draw.pFrames() - draw.aFrames();
            if (draw.aDelay() != null && draw.pDelay() != null) {
                delayGap += draw.aDelay() - draw.pDelay();
            } else {
                missingDelay = true;
            }
            settleGap += draw.aSettle() - draw.pSettle();
        }
        return new double[] {
            pBytes == 0 ? Double.NaN : (pBytes - aBytes) * 100.0 / pBytes,
            frameGap * 100.0 / (1500 * N),
            pGood == 0 ? Double.NaN : aGood * 1.0 / pGood,
            missingDelay ? Double.NaN : delayGap / (N * 1_000_000.0),
            settleGap / (double) N,
        };
    }

    static Long nullable(String field) {
        return field.isBlank() ? null : Long.valueOf(field);
    }

    static Pair parse(String[] col) {
        return new Pair(Long.parseLong(col[1]), Long.parseLong(col[2]), Long.parseLong(col[3]), nullable(col[4]),
            Long.parseLong(col[5]), Long.parseLong(col[6]), Long.parseLong(col[7]), nullable(col[8]),
            Long.parseLong(col[9]), Long.parseLong(col[11]), col[10], col[12]);
    }

    static double rank(double[] sorted, double q) {
        return sorted[(int) Math.ceil(q * sorted.length) - 1];
    }

    public static void main(String[] args) throws Exception {
        var rows = Files.readAllLines(Path.of(args[0]));
        if (rows.size() != N + 1) {
            throw new AssertionError("unexpected pair count");
        }
        Pair[] original = new Pair[N];
        for (int i = 0; i < N; i++) {
            String[] columns = rows.get(i + 1).split(",", -1);
            if (Integer.parseInt(columns[0]) != 630201 + i) {
                throw new AssertionError("seeds not in expected order");
            }
            original[i] = parse(columns);
        }
        double[] point = metrics(original);
        var bootstrap = new SplittableRandom(2026111401L);
        double[][] sampled = new double[NAMES.length][REPS];
        int[] undefined = new int[NAMES.length];
        var draws = new Pair[N];
        for (int r = 0; r < REPS; r++) {
            for (int j = 0; j < N; j++) {
                draws[j] = original[bootstrap.nextInt(N)];
            }
            double[] result = metrics(draws);
            for (int j = 0; j < NAMES.length; j++) {
                sampled[j][r] = result[j];
                if (Double.isNaN(result[j])) {
                    undefined[j]++;
                }
            }
        }
        for (int j = 0; j < NAMES.length; j++) {
            if (undefined[j] > 0 || Double.isNaN(point[j])) {
                System.out.printf("%s inconclusive; undefined=%d%n", NAMES[j], undefined[j]);
            } else {
                Arrays.sort(sampled[j]);
                System.out.printf("%s point=%.12f lower95=%.12f upper95=%.12f two-sided=[%.12f,%.12f]%n",
                    NAMES[j], point[j], rank(sampled[j], 0.05), rank(sampled[j], 0.95),
                    rank(sampled[j], 0.025), rank(sampled[j], 0.975));
            }
        }
        if (!original[0].pState().equals("none")) {
            int periodic = 0;
            int adaptive = 0;
            var states = new java.util.TreeMap<String, Integer>();
            for (Pair pair : original) {
                periodic += pair.pState().equals("settled") ? 1 : 0;
                adaptive += pair.aState().equals("settled") ? 1 : 0;
                states.merge("periodic:" + pair.pState(), 1, Integer::sum);
                states.merge("adaptive:" + pair.aState(), 1, Integer::sum);
            }
            double bound = rank(sampled[4], 0.95);
            System.out.printf("settling periodic=%d adaptive=%d count_pass=%s time_pass=%s %s%n",
                periodic, adaptive, periodic >= 27 && adaptive >= 27 && adaptive >= periodic,
                bound <= 200, states);
        }
    }
}
