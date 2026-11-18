package org.bandwidthcontrol;

import java.nio.file.Path;
import org.junit.jupiter.api.Test;
import static org.bandwidthcontrol.HybridControlTest.ns;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNull;

class FeedbackScheduleTest {
    private static final Path PROFILE = Path.of("config/profiles/controller-candidate.json");

    @Test
    void eventRequiresConsecutiveHighSamplesAndSpacingAndExitsAfterTwoLow() throws Exception {
        var schedule = schedule(SimulationArm.ADAPTIVE);
        assertNull(observe(schedule, 50, 12_000));
        assertNull(observe(schedule, 100, 7000));
        assertNull(observe(schedule, 150, 12_000));
        assertEquals("event", observe(schedule, 200, 12_000));
        schedule.sent(ns(200), ns(200));
        assertNull(observe(schedule, 250, 15_000));
        assertEquals("event", observe(schedule, 300, 15_000));
        schedule.sent(ns(300), ns(300));
        assertNull(observe(schedule, 350, 0));
        assertNull(observe(schedule, 400, 4000));
        assertNull(observe(schedule, 450, 12_000));
        assertEquals("event", observe(schedule, 500, 12_000));
    }

    @Test
    void heartbeatCoincidenceUsesOneReportAndBlockedEventRetriesOnNextSample() throws Exception {
        var schedule = schedule(SimulationArm.ADAPTIVE);
        assertNull(observe(schedule, 450, 12_000));
        assertEquals("heartbeat", observe(schedule, 500, 12_000));
        schedule.sent(ns(500), ns(500));
        assertNull(observe(schedule, 550, 12_000));
        assertEquals("event", observe(schedule, 600, 12_000));
        schedule.blocked("event");
        assertEquals("event", observe(schedule, 650, null));
        schedule.sent(ns(650), ns(650));
        assertNull(observe(schedule, 700, 12_000));
        assertEquals("event", observe(schedule, 750, 12_000));
    }

    @Test
    void quietScheduleUsesHeartbeatAndMatchedBudgetUsesConfiguredPeriod() throws Exception {
        var adaptive = schedule(SimulationArm.ADAPTIVE);
        var matched = schedule(SimulationArm.MATCHED_BUDGET);
        for (int sample = 50; sample < 500; sample += 50) {
            assertNull(observe(adaptive, sample, 4000));
            var decision = observe(matched, sample, 12_000);
            if (sample == 300) {
                assertEquals("periodic", decision);
                matched.sent(ns(sample), ns(sample));
            } else {
                assertNull(decision);
            }
        }
        assertEquals("heartbeat", observe(adaptive, 500, null));
    }

    private static FeedbackSchedule schedule(SimulationArm arm) throws Exception {
        return arm.feedback(SimulationConfig.profile(PROFILE).value());
    }

    private static String observe(FeedbackSchedule schedule, int sampleMs, Integer delay) {
        var snapshot = new Datagrams.Feedback(0, ns(sampleMs), 11_360, 20, 0, delay, null);
        return schedule.observe(snapshot, ns(sampleMs));
    }
}
