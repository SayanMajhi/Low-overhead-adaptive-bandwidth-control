package org.bandwidthcontrol;

import java.io.IOException;
import java.nio.ByteBuffer;
import java.util.function.LongSupplier;

final class Receiver {
    private static final long SAMPLE = ReceiverMeasurements.SAMPLE_NS;
    private static final long NO_TIME = ReceiverMeasurements.NO_TIME;
    private final FeedbackSchedule schedule;

    private final long session;
    private final Events events;
    private final ReceiverMeasurements measurements = new ReceiverMeasurements();
    private final ByteBuffer sendBuffer = ByteBuffer.allocate(Datagrams.FEEDBACK_BYTES);
    private long nextSample = NO_TIME;
    private long nextId;
    private long received;

    Receiver(long session, Events events) {
        this(session, events, new FeedbackSchedule(100_000_000));
    }

    Receiver(long session, Events events, FeedbackSchedule schedule) {
        this.schedule = schedule;
        this.session = session;
        this.events = events;
    }

    long nextWake() {
        return Math.min(nextSample, measurements.nextGapExpiry());
    }

    void receive(ByteBuffer buffer, long now) throws IOException {
        Datagrams.Media packet;
        try {
            packet = Datagrams.readMedia(buffer, session);
        } catch (Datagrams.InvalidPacket invalid) {
            events.note(now, "media_rejected", invalid.getMessage());
            return;
        }
        var admission = measurements.receive(packet, now);
        String event;
        switch (admission.kind()) {
            case "duplicate" -> event = "media_duplicate";
            case "identity_conflict", "outside_history" -> event = "media_rejected";
            default -> {
                event = "media_received";
                received++;
            }
        }
        events.write(now, event, packet.sequence(), packet.frame(), packet.index(), null, null, Datagrams.MEDIA_BYTES,
                     Datagrams.MEDIA_IP_BYTES, admission.kind(), null, null, null);
        if (admission.discontinuity()) {
            events.note(now, "receiver_discontinuity", "history_pressure");
        }
        if (nextSample == NO_TIME) {
            nextSample = now + SAMPLE;
        }
    }

    void service(Transport transport, LongSupplier clock, long now, long stop) throws IOException {
        measurements.advance(now, true);
        if (now < nextSample) {
            return;
        }
        long anchor = measurements.anchor();
        long sample = anchor + ((now - anchor) / SAMPLE) * SAMPLE;
        nextSample = sample + SAMPLE;
        long sendNs = clock.getAsLong();
        if (sendNs >= stop) {
            return;
        }
        Datagrams.Feedback report = measurements.snapshot(nextId, sample, sendNs);
        String reason = schedule.observe(report, sendNs - anchor);
        if (reason == null) {
            return;
        }
        ByteBuffer data = Datagrams.feedback(sendBuffer, session, report);
        int bytes;
        try {
            bytes = transport.write(data);
        } catch (IOException failure) {
            events.writeReport(sendNs, "feedback_send_failed", report, null, null, "io", null, null);
            throw failure;
        }
        if (bytes == 0) {
            schedule.blocked(reason);
            events.writeReport(sendNs, "feedback_send_retry", report, null, null, "backpressure", null, null);
            return;
        }
        nextId++;
        schedule.sent(report.sampleNs(), sendNs - anchor);
        events.writeReport(sendNs, "feedback_sent", report, null, bytes, reason, null, null);
    }

    void drain(long now) {
        measurements.advance(now, true);
    }

    long reported() {
        return nextId;
    }

    long received() {
        return received;
    }

}
