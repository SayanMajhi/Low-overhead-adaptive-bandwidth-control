package org.bandwidthcontrol;

import java.io.IOException;
import java.nio.ByteBuffer;
import java.util.function.LongSupplier;
import java.util.ArrayDeque;
import java.util.Iterator;

final class Sender {
    static final int MIN_RATE = 500_000;
    static final int MAX_RATE = 2_272_000;
    private static final long FRAME_NS = 20_000_000;
    private static final long DEADLINE_NS = 150_000_000;
    private static final long TICK_NS = 100_000_000;
    private static final int BASE_RATE = 454_400;
    private static final int SLOT_RATE = 227_200;
    private static final int MAX_QUEUE_PACKETS = 80;

    private record Packet(long frame, int index, long createdNs) {}

    private final long session;
    private int rate;
    private final RateController controller;
    private RateController.Observation latestObservation;
    private final Events events;
    private final Pacer pacer;
    private final ByteBuffer sendBuffer = ByteBuffer.allocate(Datagrams.MEDIA_BYTES);
    private final ArrayDeque<Packet> queue = new ArrayDeque<>();
    private final long[] sendTimes = new long[Datagrams.HISTORY_SIZE];
    private long nextFrame;
    private long nextSequence;
    private int remainder;
    private long nextTick = TICK_NS;
    private long latestReportId = -1;
    private long latestReportSample = -1;
    private long latestReceipt;

    Sender(long session, int rate, Events events) {
        this(session, rate, events, null);
    }

    Sender(long session, int rate, Events events, RateController controller) {
        this.controller = controller;
        this.session = session;
        this.rate = rate;
        this.events = events;
        pacer = new Pacer(Datagrams.MEDIA_IP_BYTES, rate);
    }

    long nextWake() {
        long nextSend = queue.isEmpty() ? Long.MAX_VALUE : pacer.nextNs();
        long expiry = queue.isEmpty() ? Long.MAX_VALUE : queue.getFirst().createdNs() + DEADLINE_NS;
        return Math.min(Math.min(nextSend, expiry), Math.min(nextFrame * FRAME_NS, nextTick));
    }

    void receive(ByteBuffer buffer, long now) throws IOException {
        Datagrams.Feedback report;
        Long rtt;
        String echoState;
        try {
            report = Datagrams.readFeedback(buffer, session);
            if (report.id() <= latestReportId || report.sampleNs() <= latestReportSample) {
                throw new Datagrams.InvalidPacket("old_report");
            }
            Datagrams.Echo echo = report.echo();
            if (echo == null) {
                rtt = null;
                echoState = "no_echo";
            } else if (echo.sequence() >= nextSequence) {
                throw new Datagrams.InvalidPacket("unknown_echo_sequence");
            } else if (nextSequence - echo.sequence() > sendTimes.length) {
                rtt = null;
                echoState = "echo_outside_history";
            } else if (sendTimes[(int) (echo.sequence() % sendTimes.length)] != echo.sendNs()) {
                throw new Datagrams.InvalidPacket("echo_timestamp");
            } else {
                long computedRtt = now - echo.sendNs() - echo.holdUs() * 1000;
                rtt = computedRtt >= 0 ? computedRtt : null;
                echoState = rtt == null ? "echo_negative_rtt" : "echo_valid";
            }
        } catch (Datagrams.InvalidPacket invalid) {
            events.note(now, "feedback_rejected", invalid.getMessage());
            return;
        }
        latestReportId = report.id();
        latestReportSample = report.sampleNs();
        latestReceipt = now;
        latestObservation = new RateController.Observation(report, rtt != null);
        events.writeReport(now, "feedback_received", report, rate, Datagrams.FEEDBACK_BYTES, echoState, 0L, rtt);
    }

    void service(Transport transport, LongSupplier clock, long now, long stop) throws IOException {
        if (now >= nextTick) {
            nextTick = (now / TICK_NS + 1) * TICK_NS;
            String reason = latestReportId < 0 ? "no_feedback" : "fixed_rate";
            if (controller != null) {
                var decision = controller.decide(now, rate, latestObservation);
                rate = decision.rate();
                reason = decision.reason();
                latestObservation = null;
                pacer.rate(rate);
            }
            events.write(now, "rate_tick", null, null, null, latestReportId < 0 ? null : latestReportId,
                         rate, null, null, reason,
                         latestReportId < 0 ? null : now - latestReceipt,
                         latestReportId < 0 ? null : latestReportSample, null);
        }
        while (!queue.isEmpty() && queue.getFirst().createdNs() + DEADLINE_NS <= now) {
            discard(queue.removeFirst(), now, "deadline");
        }
        while (nextFrame * FRAME_NS <= now) {
            generate(now);
        }
        if (queue.isEmpty() || now < pacer.nextNs()) {
            return;
        }
        Packet packet = queue.getFirst();
        long sendNs = clock.getAsLong();
        if (sendNs >= stop || packet.createdNs() + DEADLINE_NS <= sendNs) {
            return;
        }
        ByteBuffer data = Datagrams.media(sendBuffer, session,
                new Datagrams.Media(nextSequence, sendNs, packet.frame(), packet.index()));
        int bytes;
        try {
            bytes = transport.write(data);
        } catch (IOException failure) {
            packetEvent(sendNs, "media_send_failed", packet, nextSequence, null, "io");
            throw failure;
        }
        if (bytes == 0) {
            pacer.retry(sendNs);
            packetEvent(sendNs, "media_send_retry", packet, nextSequence, null, "backpressure");
            return;
        }
        queue.removeFirst();
        long sequence = nextSequence++;
        sendTimes[(int) (sequence % sendTimes.length)] = sendNs;
        pacer.sent(clock.getAsLong());
        packetEvent(sendNs, "media_sent", packet, sequence, bytes, "sent");
    }

    private void generate(long now) throws IOException {
        long frame = nextFrame++;
        long created = frame * FRAME_NS;
        events.write(created, "frame_created", null, frame, null, null, rate, null, null, "source",
                     null, null, null);
        int slots = remainder + rate - BASE_RATE;
        int chosen = 2 + slots / SLOT_RATE;
        remainder = slots % SLOT_RATE;
        for (int index = 0; index < 10; index++) {
            Packet packet = new Packet(frame, index, created);
            if (index >= chosen) {
                discard(packet, now, "unselected_enhancement");
            } else if (created + DEADLINE_NS <= now) {
                discard(packet, now, "deadline");
            } else {
                admit(packet, now);
            }
        }
    }

    private void admit(Packet packet, long now) throws IOException {
        if (queue.size() == MAX_QUEUE_PACKETS && packet.index() < 2) {
            Iterator<Packet> newestFirst = queue.descendingIterator();
            while (newestFirst.hasNext()) {
                Packet candidate = newestFirst.next();
                if (candidate.index() >= 2) {
                    newestFirst.remove();
                    discard(candidate, now, "queue_evicted_enhancement");
                    break;
                }
            }
        }
        if (queue.size() == MAX_QUEUE_PACKETS) {
            discard(packet, now, "queue_full");
        } else {
            queue.addLast(packet);
        }
    }

    void close(long now, long stop) throws IOException {
        // Record due frame identities even if the loop last woke just before the run cutoff.
        while (nextFrame * FRAME_NS < Math.min(now, stop)) {
            generate(Math.min(now, stop));
        }
        while (!queue.isEmpty()) {
            Packet packet = queue.removeFirst();
            discard(packet, now, packet.createdNs() + DEADLINE_NS <= now ? "deadline" : "shutdown");
        }
    }

    long sent() {
        return nextSequence;
    }

    long frames() {
        return nextFrame;
    }

    private void discard(Packet packet, long now, String reason) throws IOException {
        packetEvent(now, "source_discard", packet, null, null, reason);
    }

    private void packetEvent(long now, String event, Packet packet, Long sequence, Integer bytes, String reason)
            throws IOException {
        events.write(now, event, sequence, packet.frame(), packet.index(), null, rate, bytes,
                     bytes == null ? null : bytes + Datagrams.UDP_IP_BYTES, reason, null, null, null);
    }
}
