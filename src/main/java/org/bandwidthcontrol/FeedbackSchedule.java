package org.bandwidthcontrol;

final class FeedbackSchedule {
    private final long period;
    private final SimulationConfig.Profile profile;
    private final SimulationConfig.Hybrid event;
    private long nextPeriodic;
    private long lastSent;
    private long lastSample;
    private int consecutiveHigh;
    private int consecutiveLow;
    private boolean congested;
    private boolean retryEvent;

    FeedbackSchedule(long period) {
        this(period, null, null);
    }

    FeedbackSchedule(long period, SimulationConfig.Profile profile, SimulationConfig.Hybrid event) {
        this.period = period;
        this.profile = profile;
        this.event = event;
        nextPeriodic = period;
    }

    String observe(Datagrams.Feedback snapshot, long now) {
        long sample = snapshot.sampleNs();
        if (event == null) {
            return sample >= nextPeriodic ? "periodic" : null;
        }
        if (sample - lastSample != ReceiverMeasurements.SAMPLE_NS) {
            consecutiveHigh = 0;
            consecutiveLow = 0;
        }
        lastSample = sample;
        Congestion signal = Congestion.classify(profile, event, snapshot);
        if (signal == Congestion.HIGH) {
            consecutiveHigh = Math.min(event.enterSamples(), consecutiveHigh + 1);
            consecutiveLow = 0;
            if (consecutiveHigh == event.enterSamples()) {
                congested = true;
            }
        } else if (signal == Congestion.LOW) {
            consecutiveHigh = 0;
            consecutiveLow = Math.min(event.exitSamples(), consecutiveLow + 1);
            if (consecutiveLow == event.exitSamples()) {
                congested = false;
            }
        } else {
            consecutiveHigh = 0;
            consecutiveLow = 0;
        }
        long since = now - lastSent;
        if (since >= SimulationConfig.ns(event.heartbeatMs())) {
            return "heartbeat";
        }
        return retryEvent || (congested && signal == Congestion.HIGH
            && since >= SimulationConfig.ns(event.minEventSpacingMs())) ? "event" : null;
    }

    void blocked(String reason) {
        retryEvent = reason.equals("event");
    }

    void sent(long sample, long now) {
        retryEvent = false;
        if (event == null) {
            nextPeriodic = (sample / period + 1) * period;
        } else {
            lastSent = now;
        }
    }
}
