package org.bandwidthcontrol;

import java.nio.ByteBuffer;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;

/** Independent exact-clock fixture for production controller and scheduling classes. */
public class ControllerFixture {
    private static final long MS = 1_000_000L;
    private static final int MBPS = 1_000_000;

    private static void equal(Object expected, Object actual) {
        if (!java.util.Objects.equals(expected, actual)) {
            throw new AssertionError("expected " + expected + ", observed " + actual);
        }
    }

    private static Datagrams.Feedback report(long sampleMs, int bytes, int received,
                                               int missing, Integer delayUs, long echoNs) {
        return new Datagrams.Feedback(0, sampleMs * MS, bytes, received, missing, delayUs,
            new Datagrams.Echo(0, echoNs, 0));
    }

    private static void classify(SimulationConfig.Profile profile, SimulationConfig.Hybrid hybrid) {
        equal(Congestion.HIGH, Congestion.classify(profile, hybrid, report(0, 0, 19, 1, null, 0)));
        equal(Congestion.LOW, Congestion.classify(profile, hybrid, report(0, 0, 49, 1, 4_000, 0)));
        equal(Congestion.NEUTRAL, Congestion.classify(profile, hybrid, report(0, 0, 8, 1, null, 0)));
        equal(Congestion.HIGH, Congestion.classify(profile, hybrid, report(0, 0, 0, 0, 12_000, 0)));
        equal(Congestion.NEUTRAL, Congestion.classify(profile, hybrid, report(0, 0, 50, 0, 11_999, 0)));
        equal(Congestion.NEUTRAL, Congestion.classify(profile, hybrid, report(0, 0, 50, 0, 4_001, 0)));
    }

    private static void schedules(SimulationConfig.Profile profile, SimulationConfig.Hybrid hybrid) {
        var schedule = new FeedbackSchedule(0, profile, hybrid);
        Integer high = 12_000;
        Integer low = 4_000;
        Integer[] signals = {high, null, high, high, high, high, null, high, low, low, high};
        String[] expected = {null, null, null, "event", null, "event", null, "event", null, null, null};
        for (int index = 0; index < signals.length; index++) {
            long ms = (index + 1) * 50L;
            var sample = report(ms, 30_104, 50, 0, signals[index], 0);
            equal(expected[index], schedule.observe(sample, ms * MS));
            if (expected[index] != null) {
                schedule.sent(sample.sampleNs(), ms * MS);
            }
        }
        // The prior event was at 400 ms. Rising loss at 850/900 coincides with heartbeating.
        for (int ms = 600; ms <= 900; ms += 50) {
            Integer signal = ms >= 850 ? high : low;
            equal(ms == 900 ? "heartbeat" : null, schedule.observe(report(ms, 30_104, 50, 0, signal, 0), ms * MS));
        }
        schedule.sent(900 * MS, 900 * MS);
        equal(null, schedule.observe(report(950, 30_104, 50, 0, high, 0), 950 * MS));

        var coalesced = new FeedbackSchedule(0, profile, hybrid);
        equal(null, coalesced.observe(report(50, 30_104, 50, 0, high, 0), 50 * MS));
        equal(null, coalesced.observe(report(150, 30_104, 50, 0, high, 0), 150 * MS));
        equal("event", coalesced.observe(report(200, 30_104, 50, 0, high, 0), 200 * MS));
    }

    private static void eventBackpressure(SimulationConfig.Profile profile, SimulationConfig.Hybrid hybrid) {
        var schedule = new FeedbackSchedule(0, profile, hybrid);
        equal(null, schedule.observe(report(50, 0, 0, 0, 12_000, 0), 50 * MS));
        equal("event", schedule.observe(report(100, 0, 0, 0, 12_000, 0), 100 * MS));
        schedule.blocked("event");
        equal("event", schedule.observe(report(150, 0, 0, 0, null, 0), 150 * MS));
        schedule.sent(150 * MS, 150 * MS);
        equal(null, schedule.observe(report(200, 0, 0, 0, 12_000, 0), 200 * MS));
        equal("event", schedule.observe(report(250, 0, 0, 0, 12_000, 0), 250 * MS));
    }

    private static void handoff(SimulationConfig.Profile profile, SimulationConfig.Hybrid hybrid,
                                Path output) throws Exception {
        List<Long> reportIds = new ArrayList<>();
        List<Long> sampleTimes = new ArrayList<>();
        try (var events = new Events(output, 1)) {
            var receiver = new Receiver(1, events, new FeedbackSchedule(0, profile, hybrid));
            ByteBuffer encoded = Datagrams.media(ByteBuffer.allocate(Datagrams.MEDIA_BYTES), 1,
                new Datagrams.Media(0, 0, 0, 0));
            receiver.receive(encoded, 0);
            for (int ms = 50; ms <= 600; ms += 50) {
                final long current = ms * MS;
                receiver.service(bytes -> {
                    int length = bytes.remaining();
                    var decoded = Datagrams.readFeedback(bytes, 1);
                    reportIds.add(decoded.id());
                    sampleTimes.add(decoded.sampleNs());
                    return reportIds.size() == 1 ? 0 : length;
                }, () -> current, current, 1000 * MS);
            }
            equal(1L, receiver.reported());
        }
        equal(List.of(0L, 0L), reportIds);
        equal(List.of(500 * MS, 550 * MS), sampleTimes);
    }

    private static RateController.Observation observe(Datagrams.Feedback report) {
        return new RateController.Observation(report, true);
    }

    private static void controller(SimulationConfig.Profile profile, SimulationConfig.Hybrid hybrid) {
        var periodic = SimulationArm.PERIODIC.controller(profile.withHybrid(hybrid));
        var adaptive = SimulationArm.ADAPTIVE.controller(profile.withHybrid(hybrid));
        var noDelay = SimulationArm.NO_DELAY_PERIODIC.controller(profile.withHybrid(hybrid));
        int[] times = {200, 700, 800, 900, 1000, 1100};
        Integer[] delays = {4_000, 4_000, 12_000, 12_000, 12_000, null};
        int[] rates = {1_030_000, 1_180_000, 1_003_000, 1_003_000, 852_550, 852_550};
        String[] reasons = {"healthy_increase", "healthy_increase", "delay_decrease",
                            "congestion_cooldown", "delay_decrease", "neutral_feedback"};
        int rate = MBPS;
        for (int i = 0; i < times.length; i++) {
            var snapshot = observe(report(times[i] - 40, 30_104, 50, 0, delays[i], (times[i] - 50) * MS));
            var fixedDecision = periodic.decide(times[i] * MS, rate, snapshot);
            equal(new RateController.Decision(rates[i], reasons[i]), fixedDecision);
            equal(fixedDecision, adaptive.decide(times[i] * MS, rate, snapshot));
            rate = fixedDecision.rate();
        }
        equal(new RateController.Decision(1_030_000, "healthy_increase"),
            noDelay.decide(800 * MS, MBPS, observe(report(760, 30_104, 50, 0, 12_000, 750 * MS))));

        var boundary = new RateController(profile, hybrid, hybrid.decreasePermille());
        equal(new RateController.Decision(1_030_000, "healthy_increase"),
            boundary.decide(800 * MS, MBPS, observe(report(750, 30_104, 50, 0, 4_000, 50 * MS))));
        equal(new RateController.Decision(1_030_000, "ineligible_feedback"),
            boundary.decide(900 * MS, 1_030_000,
                observe(report(850, 30_104, 50, 0, 4_000, 150 * MS - 1))));
        equal(new RateController.Decision(1_060_000, "healthy_increase"),
            boundary.decide(1_000 * MS, 1_030_000,
                observe(report(950, 30_104, 50, 0, 4_000, 900 * MS))));

        var missing = new RateController(profile, hybrid, hybrid.decreasePermille());
        equal(new RateController.Decision(MBPS, "neutral_feedback"),
            missing.decide(800 * MS, MBPS, observe(report(760, 0, 0, 1, null, 750 * MS))));
        equal(new RateController.Decision(MBPS, "no_new_feedback"), missing.decide(900 * MS, MBPS, null));

        var empty = new RateController(profile, hybrid, hybrid.decreasePermille());
        equal(new RateController.Decision(MBPS, "ineligible_feedback"),
            empty.decide(800 * MS, MBPS, observe(report(760, 0, 0, 0, null, 750 * MS))));
        equal(new RateController.Decision(850_000, "stale_decrease"), empty.decide(900 * MS, MBPS, null));
        equal(new RateController.Decision(850_000, "no_new_feedback"),
            empty.decide(1_300 * MS, 850_000, null));
        equal(new RateController.Decision(722_500, "stale_decrease"),
            empty.decide(1_400 * MS, 850_000, null));
    }

    public static void main(String[] args) throws Exception {
        var config = SimulationConfig.profile(Path.of(args[0])).value();
        Path evidence = Path.of(args[1]);
        Files.createDirectories(evidence.getParent());
        classify(config.base(), config.hybrid());
        schedules(config.base(), config.hybrid());
        eventBackpressure(config.base(), config.hybrid());
        handoff(config.base(), config.hybrid(), evidence);
        controller(config.base(), config.hybrid());
        System.out.println("Independent classification, persistence, spacing, retry, shared control and timeout checks passed.");
    }
}
