package org.bandwidthcontrol;

final class RateController {
    record Observation(Datagrams.Feedback report, boolean validEcho) {}
    record Decision(int rate, String reason) {}

    private final SimulationConfig.Profile profile;
    private final SimulationConfig.Hybrid delay;
    private final int decreasePermille;
    private long lastEvidence;
    private long lastDecrease = -1;
    private long lastStaleDecrease = -1;
    private long lastHealthySample = -1;

    RateController(SimulationConfig.Profile profile, SimulationConfig.Hybrid delay, int decreasePermille) {
        this.profile = profile;
        this.delay = delay;
        this.decreasePermille = decreasePermille;
    }

    Decision decide(long now, int rate, Observation observation) {
        if (now - lastEvidence >= profile.staleMs() * 1_000_000L) {
            lastHealthySample = -1;
        }
        if (observation != null && fresh(now, observation)) {
            return update(now, rate, observation.report());
        }
        if (observation != null) {
            lastHealthySample = -1;
        }
        if (now - lastEvidence >= profile.staleMs() * 1_000_000L
            && eligible(now, lastStaleDecrease, profile.staleSpacingMs())) {
            lastStaleDecrease = now;
            return new Decision(clamp((int) (rate * (long) profile.stalePermille() / 1000)), "stale_decrease");
        }
        return new Decision(rate, observation == null ? "no_new_feedback" : "ineligible_feedback");
    }

    private Decision update(long now, int rate, Datagrams.Feedback report) {
        lastEvidence = now;
        var signal = Congestion.classify(profile, delay, report);
        if (signal == Congestion.HIGH) {
            lastHealthySample = -1;
            if (eligible(now, lastDecrease, profile.congestionSpacingMs())) {
                lastDecrease = now;
                return new Decision(clamp((int) (rate * (long) decreasePermille / 1000)),
                    Congestion.highLoss(profile, report) ? "loss_decrease" : "delay_decrease");
            }
            return new Decision(rate, "congestion_cooldown");
        }
        if (signal == Congestion.LOW
            && report.ipBytes() * 32L * 1000 >= (long) rate * profile.healthyDeliveryPermille()) {
            long credit = lastHealthySample < 0 ? 100_000_000L
                : Math.min(report.sampleNs() - lastHealthySample, profile.maxGrowthCreditMs() * 1_000_000L);
            lastHealthySample = report.sampleNs();
            int gain = (int) (credit * profile.additiveBpsPerSecond() / 1_000_000_000L);
            return new Decision(clamp(rate + gain), "healthy_increase");
        }
        lastHealthySample = -1;
        return new Decision(rate, "neutral_feedback");
    }

    private boolean fresh(long now, Observation observation) {
        var report = observation.report();
        return observation.validEcho() && report.echo() != null && report.echo().sendNs() <= now
            && now - report.echo().sendNs() <= profile.freshMs() * 1_000_000L
            && (report.ipBytes() > 0 || report.received() > 0 || report.missing() > 0);
    }

    private boolean eligible(long now, long previous, int spacingMs) {
        return previous < 0 || now - previous >= spacingMs * 1_000_000L;
    }

    private int clamp(int rate) {
        return Math.clamp(rate, profile.minBps(), profile.maxBps());
    }
}
