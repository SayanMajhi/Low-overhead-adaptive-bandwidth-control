package org.bandwidthcontrol;

import static org.junit.jupiter.api.Assertions.assertEquals;
import java.nio.file.Path;
import org.junit.jupiter.api.Test;

class AimdTest {
    private final Path profilePath = Path.of("config/profiles/baselines.json");

    @Test
    void silenceUsesStartupAndSpacingThresholds() throws Exception {
        var aimd = SimulationArm.AIMD.controller(SimulationConfig.profile(profilePath).value());
        assertEquals(1_000_000, aimd.decide(800_000_000, 1_000_000, null).rate());
        assertEquals(850_000, aimd.decide(900_000_000, 1_000_000, null).rate());
        assertEquals(850_000, aimd.decide(1_300_000_000, 850_000, null).rate());
        assertEquals(722_500, aimd.decide(1_400_000_000, 850_000, null).rate());
    }

    @Test
    void qualifiedLossDecreasesAtFirstEligibleDecision() throws Exception {
        var aimd = SimulationArm.AIMD.controller(SimulationConfig.profile(profilePath).value());
        var loss = new RateController.Observation(new Datagrams.Feedback(0, 100_000_000, 5680, 9, 1, null,
            new Datagrams.Echo(0, 10_000_000, 0)), true);
        assertEquals(750_000, aimd.decide(200_000_000, 1_000_000, loss).rate());
        assertEquals(750_000, aimd.decide(300_000_000, 750_000, loss).rate());
    }

    @Test
    void rateTimesLargeDecreaseFactorUsesLongArithmetic() {
        var profile = new SimulationConfig.Profile(1, "test", 100, 1_400_000, 1_000_000, 500_000, 2_272_000,
            10, 50, 20, 750, 750, 850, 500, 200, 500, 300_000, 999, 999);
        var aimd = new RateController(profile, null, profile.aimdDecreasePermille());
        assertEquals(2_269_728, aimd.decide(900_000_000, 2_272_000, null).rate());
    }
}
