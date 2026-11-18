package org.bandwidthcontrol;

import java.io.IOException;
import java.util.ArrayDeque;

final class SimulatedLink {
    static final long NONE = Long.MAX_VALUE;
    static final long WORK_PER_IP_BYTE = 8_000_000_000L;
    record Packet(String stream, long id, int bytes, byte[] data) {}
    record Service(long from, long until, long work, int rate, Packet packet) {}

    interface Observer {
        void event(long now, String event, Packet packet, int waitingBytes, String reason) throws IOException;
        void service(Service service) throws IOException;
        void complete(long now, Packet packet) throws IOException;
    }

    private final int limit;
    private final ArrayDeque<Packet> queue = new ArrayDeque<>();
    private final Observer observer;
    private int rate;
    private int waitingBytes;
    private Packet active;
    private long remaining;
    private long updatedAt;

    SimulatedLink(int initialRate, int limit, Observer observer) {
        rate = initialRate;
        this.limit = limit;
        this.observer = observer;
    }

    long nextCompletion() {
        return active == null ? NONE : updatedAt + Math.ceilDiv(remaining, rate);
    }

    void offer(long now, Packet packet) throws IOException {
        if (active == null) {
            observer.event(now, "admitted", packet, waitingBytes, "direct");
            start(now, packet);
        } else if (packet.bytes() <= limit - waitingBytes) {
            queue.addLast(packet);
            waitingBytes += packet.bytes();
            observer.event(now, "admitted", packet, waitingBytes, "queued");
        } else {
            observer.event(now, "drop", packet, waitingBytes, "queue");
        }
    }

    void advance(long now) throws IOException {
        if (active == null || now <= updatedAt) {
            return;
        }
        long work = Math.min(remaining, (now - updatedAt) * rate);
        observer.service(new Service(updatedAt, now, work, rate, active));
        remaining -= work;
        updatedAt = now;
        if (remaining == 0) {
            Packet packet = active;
            active = null;
            observer.event(now, "serialized", packet, waitingBytes, "");
            observer.complete(now, packet);
            if (!queue.isEmpty()) {
                Packet first = queue.removeFirst();
                waitingBytes -= first.bytes();
                start(now, first);
            }
        }
    }

    void rate(int nextRate) {
        rate = nextRate;
    }

    void cutoff(long now) throws IOException {
        if (active != null) {
            observer.event(now, "undelivered", active, waitingBytes, "in_service_at_cutoff");
        }
        for (Packet packet : queue) {
            observer.event(now, "undelivered", packet, waitingBytes, "queued_at_cutoff");
        }
    }

    private void start(long now, Packet packet) throws IOException {
        updatedAt = now;
        active = packet;
        remaining = packet.bytes() * WORK_PER_IP_BYTE;
        observer.event(now, "service_started", packet, waitingBytes, "");
    }
}
