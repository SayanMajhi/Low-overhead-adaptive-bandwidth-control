package org.bandwidthcontrol;

import java.nio.file.Path;
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNull;

class HybridControlTest {
    private static final Path PROFILE = Path.of("config/profiles/controller-candidate.json");

    @Test
    void samePrimaryControllerRespondsToDelayAndAblationIgnoresIt() throws Exception {
        var config = SimulationConfig.profile(PROFILE).value();
        var periodic = SimulationArm.PERIODIC.controller(config);
        var adaptive = SimulationArm.ADAPTIVE.controller(config);
        var noDelay = SimulationArm.NO_DELAY_PERIODIC.controller(config);
        var onlyDelay = observation(100, 90, 12_000, 42, 0);
        assertEquals(periodic.decide(ns(100), 1_000_000, onlyDelay), adaptive.decide(ns(100), 1_000_000, onlyDelay));
        assertEquals(new RateController.Decision(850_000, "delay_decrease"),
            periodic.decide(ns(300), 1_000_000, observation(300, 290, 12_000, 42, 0)));
        assertEquals(1_030_000, noDelay.decide(ns(100), 1_000_000, onlyDelay).rate());
        assertEquals(new RateController.Decision(850_000, "loss_decrease"),
            noDelay.decide(ns(300), 1_000_000, observation(300, 290, null, 18, 2)));
    }

    @Test
    void growthUsesSampleElapsedTimeAndNeutralClearsCredit() throws Exception {
        var config = SimulationConfig.profile(PROFILE).value();
        var controller = SimulationArm.PERIODIC.controller(config);
        assertEquals(1_030_000, controller.decide(ns(100), 1_000_000, observation(100, 90, 3000, 42, 0)).rate());
        assertEquals(1_030_000, controller.decide(ns(200), 1_030_000, null).rate());
        assertEquals(1_180_000, controller.decide(ns(600), 1_030_000, observation(600, 590, 4000, 47, 0)).rate());
        assertEquals(new RateController.Decision(1_180_000, "neutral_feedback"),
            controller.decide(ns(700), 1_180_000, observation(700, 690, null, 47, 0)));
        assertEquals(1_210_000, controller.decide(ns(800), 1_180_000, observation(800, 790, 4000, 49, 0)).rate());
    }

    @Test
    void staleAndEmptyEvidenceCannotRefreshButFinalizedLossCan() throws Exception {
        var config = SimulationConfig.profile(PROFILE).value();
        var controller = SimulationArm.ADAPTIVE.controller(config);
        assertEquals("ineligible_feedback", controller.decide(ns(800), 1_000_000, observation(100, 49, 0, 40, 0)).reason());
        var finalizedOnly = new Datagrams.Feedback(1, ns(850), 0, 0, 1, null, new Datagrams.Echo(0, ns(800), 0));
        assertEquals("neutral_feedback",
            controller.decide(ns(900), 1_000_000, new RateController.Observation(finalizedOnly, true)).reason());
        var empty = new Datagrams.Feedback(2, ns(1700), 0, 0, 0, null, new Datagrams.Echo(0, ns(1600), 0));
        assertEquals(850_000,
            controller.decide(ns(1800), 1_000_000, new RateController.Observation(empty, true)).rate());
    }

    private static RateController.Observation observation(int sampleMs, int echoMs, Integer delay, int received,
                                                         int missing) {
        var feedback = new Datagrams.Feedback(sampleMs / 100, ns(sampleMs), received * Datagrams.MEDIA_IP_BYTES,
            received, missing, delay, new Datagrams.Echo(0, ns(echoMs), 0));
        return new RateController.Observation(feedback, true);
    }

    static long ns(int ms) {
        return ms * 1_000_000L;
    }
}
