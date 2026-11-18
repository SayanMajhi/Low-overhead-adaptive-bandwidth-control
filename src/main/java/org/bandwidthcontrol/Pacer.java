package org.bandwidthcontrol;

final class Pacer {
    private final long bitNanoseconds;
    private int rateBps;
    private long nextNs;

    Pacer(int ipBytes, int rateBps) {
        bitNanoseconds = ipBytes * 8L * 1_000_000_000;
        this.rateBps = rateBps;
    }

    long nextNs() {
        return nextNs;
    }

    void sent(long now) {
        nextNs = now + Math.ceilDiv(bitNanoseconds, rateBps);
    }

    void rate(int rateBps) {
        this.rateBps = rateBps;
    }

    void retry(long now) {
        nextNs = now + 1_000_000;
    }
}
