package org.bandwidthcontrol;

import java.nio.ByteBuffer;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.HexFormat;
import java.util.SplittableRandom;

final class ImpairmentTrace {
    record State(String dropReason, long propagationNs) {}

    private final int[][] jitterUs;
    private final boolean[][] burst;
    private final boolean[][] isolated;
    private final String hash;

    ImpairmentTrace(SimulationConfig.Scenario scenario, long seed) {
        var root = new SplittableRandom(seed);
        var draws = new SplittableRandom[6];
        for (int i = 0; i < draws.length; i++) {
            draws[i] = root.split();
        }
        var paths = new SimulationConfig.PathSettings[] {scenario.forward(), scenario.reverse()};
        int slots = scenario.stopMs() + scenario.drainMs() + 1;
        jitterUs = new int[2][slots];
        burst = new boolean[2][slots];
        isolated = new boolean[2][slots];
        var digest = sha256();
        var raw = ByteBuffer.allocate(24);
        for (int slot = 0; slot < slots; slot++) {
            raw.clear();
            for (int i = 0; i < paths.length; i++) {
                var path = paths[i];
                int extent = 1000 * path.jitterMs();
                int jitter = draws[i * 3].nextInt(-extent, extent + 1);
                int loss = draws[i * 3 + 1].nextInt(1_000_000);
                int transition = draws[i * 3 + 2].nextInt(1_000_000);
                raw.putInt(jitter).putInt(loss).putInt(transition);
                jitterUs[i][slot] = jitter;
                isolated[i][slot] = loss < path.lossPpm();
                if (slot > 0) {
                    burst[i][slot] = burst[i][slot - 1] ? transition >= path.burstExitPpm()
                                                       : transition < path.burstEnterPpm();
                }
            }
            digest.update(raw.array());
        }
        hash = HexFormat.of().formatHex(digest.digest());
    }

    String hash() {
        return hash;
    }

    State state(boolean forward, SimulationConfig.PathSettings path, long now) {
        int slot = (int) (now / SimulationConfig.NS_PER_MS);
        int direction = forward ? 0 : 1;
        for (var blackout : path.blackouts()) {
            if (blackout.fromMs() <= slot && slot < blackout.untilMs()) {
                return new State("blackout", 0);
            }
        }
        if (burst[direction][slot]) {
            return new State("burst", 0);
        }
        if (isolated[direction][slot]) {
            return new State("isolated", 0);
        }
        long delay = SimulationConfig.ns(path.delayMs()) + jitterUs[direction][slot] * 1000L;
        for (var extra : path.extraDelay()) {
            if (extra.fromMs() <= slot && slot < extra.untilMs()) {
                delay += SimulationConfig.ns(extra.addMs());
                break;
            }
        }
        return new State(null, Math.max(delay, 1));
    }

    static String sha256(byte[] bytes) {
        return HexFormat.of().formatHex(sha256().digest(bytes));
    }

    private static MessageDigest sha256() {
        try {
            return MessageDigest.getInstance("SHA-256");
        } catch (NoSuchAlgorithmException impossible) {
            throw new IllegalStateException(impossible);
        }
    }
}
