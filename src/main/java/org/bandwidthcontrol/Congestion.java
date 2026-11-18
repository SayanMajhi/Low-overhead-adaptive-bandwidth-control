package org.bandwidthcontrol;

enum Congestion {
    HIGH, LOW, NEUTRAL;

    static Congestion classify(SimulationConfig.Profile profile, SimulationConfig.Hybrid delay, Datagrams.Feedback report) {
        Integer timing = report.timingUs();
        if (highLoss(profile, report) || (delay != null && timing != null && timing >= delay.delayHighUs())) {
            return HIGH;
        }
        int outcomes = report.received() + report.missing();
        boolean lowLoss = outcomes >= profile.minOutcomes()
            && report.missing() * 1000 <= outcomes * profile.lowLossPermille();
        if (lowLoss && (delay == null || (timing != null && timing <= delay.delayLowUs()))) {
            return LOW;
        }
        return NEUTRAL;
    }

    static boolean highLoss(SimulationConfig.Profile profile, Datagrams.Feedback report) {
        int outcomes = report.received() + report.missing();
        return outcomes >= profile.minOutcomes() && report.missing() * 1000 >= outcomes * profile.highLossPermille();
    }
}
