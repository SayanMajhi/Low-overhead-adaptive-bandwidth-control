package org.bandwidthcontrol;

import java.util.Arrays;
import java.util.stream.Collectors;

enum SimulationArm {
    FIXED("fixed"), AIMD("aimd"), PERIODIC("periodic"), ADAPTIVE("adaptive"),
    HEARTBEAT_ONLY("heartbeat-only"), MATCHED_BUDGET("matched-budget"), NO_DELAY_PERIODIC("no-delay-periodic");

    private final String id;

    SimulationArm(String id) {
        this.id = id;
    }

    static SimulationArm parse(String id) {
        return Arrays.stream(values()).filter(arm -> arm.id.equals(id)).findFirst().orElseThrow(
            () -> new IllegalArgumentException("available simulation arms: "
                + Arrays.stream(values()).map(arm -> arm.id).collect(Collectors.joining(", "))));
    }

    int startRate(SimulationConfig.Profile profile) {
        return this == FIXED ? profile.fixedBps() : profile.initialBps();
    }

    RateController controller(SimulationConfig.ProfileWithHybrid config) {
        var base = config.base();
        var hybrid = config.hybrid();
        return switch (this) {
            case FIXED -> null;
            case AIMD -> new RateController(base, null, base.aimdDecreasePermille());
            case NO_DELAY_PERIODIC -> new RateController(base, null, hybrid.decreasePermille());
            default -> new RateController(base, hybrid, hybrid.decreasePermille());
        };
    }

    FeedbackSchedule feedback(SimulationConfig.ProfileWithHybrid config) {
        var base = config.base();
        var hybrid = config.hybrid();
        return switch (this) {
            case ADAPTIVE -> new FeedbackSchedule(0, base, hybrid);
            case HEARTBEAT_ONLY -> new FeedbackSchedule(SimulationConfig.ns(hybrid.heartbeatMs()));
            case MATCHED_BUDGET -> new FeedbackSchedule(SimulationConfig.ns(hybrid.matchedPeriodMs()));
            default -> new FeedbackSchedule(SimulationConfig.ns(base.periodMs()));
        };
    }

    void validate(SimulationConfig.ProfileWithHybrid config) {
        if (this != FIXED && this != AIMD && config.hybrid() == null) {
            throw new IllegalArgumentException(id + " requires a profile with hybrid settings");
        }
    }
}
