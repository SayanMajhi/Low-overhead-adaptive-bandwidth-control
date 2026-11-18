package org.bandwidthcontrol;

import java.lang.management.ManagementFactory;
import java.nio.ByteBuffer;
import java.nio.file.Path;
import java.util.Locale;

final class ComponentTiming {
    private static final long SAMPLE_NS = 50_000_000L;
    private static final long ARRIVAL_NS = 5_000_000L;
    private static final int SAMPLES = 20_000;
    private static final int REPEATS = 6;
    private static volatile long result;

    private record Counters(long observationNs, long scheduleAndEncodeNs, long senderDecisionNs,
                            long schedules, long reports, long decisions, long output) {}

    public static void main(String[] args) throws Exception {
        if (args.length != 2 || !(args[1].equals("periodic") || args[1].equals("adaptive"))) {
            throw new IllegalArgumentException("usage: ComponentTiming profile.json periodic|adaptive");
        }
        var profile = SimulationConfig.profile(Path.of(args[0])).value();
        var arm = SimulationArm.parse(args[1]);
        arm.validate(profile);
        for (int i = 0; i < 4; i++) {
            result = measure(profile, arm).output();
        }
        System.out.println("arm,repetition,observation_calls,observation_ns,schedule_calls,"
            + "schedule_encoding_ns,reports_encoded,decision_calls,decision_ns,process_cpu_ns");
        var os = (com.sun.management.OperatingSystemMXBean) ManagementFactory.getOperatingSystemMXBean();
        for (int repetition = 1; repetition <= REPEATS; repetition++) {
            long startCpu = os.getProcessCpuTime();
            Counters counts = measure(profile, arm);
            long cpu = os.getProcessCpuTime() - startCpu;
            result = counts.output();
            System.out.printf(Locale.ROOT, "%s,%d,%d,%d,%d,%d,%d,%d,%d,%d%n", args[1], repetition, SAMPLES,
                counts.observationNs(), counts.schedules(), counts.scheduleAndEncodeNs(), counts.reports(),
                counts.decisions(), counts.senderDecisionNs(), cpu);
        }
    }

    private static Counters measure(SimulationConfig.ProfileWithHybrid profile, SimulationArm arm) {
        var measurements = new ReceiverMeasurements();
        var schedule = arm.feedback(profile);
        var controller = arm.controller(profile);
        ByteBuffer buffer = ByteBuffer.allocate(Datagrams.FEEDBACK_BYTES);
        long sequence = 0;
        long now = 0;
        long reportId = 0;
        long observationNs = 0;
        long schedulingNs = 0;
        long decisionNs = 0;
        long decisionCalls = 0;
        int rate = profile.base().initialBps();
        RateController.Observation pending = null;
        long checksum = 0;
        measurements.receive(new Datagrams.Media(sequence++, 0, 0, 0), 0);
        for (int tick = 1; tick <= SAMPLES; tick++) {
            long sample = tick * SAMPLE_NS;
            while (now < sample) {
                now += ARRIVAL_NS;
                // Repeat 1 s of mild artificial transit growth every 4 s.
                long cycleMs = now / 1_000_000L % 4000;
                long hold = cycleMs < 1000 ? cycleMs * 100_000L : 0;
                long sent = Math.max(0, now - hold);
                measurements.receive(new Datagrams.Media(sequence, sent, sequence, 0), now);
                sequence++;
            }

            long mark = System.nanoTime();
            var snapshot = measurements.snapshot(reportId, sample, sample);
            observationNs += System.nanoTime() - mark;

            mark = System.nanoTime();
            String reason = schedule.observe(snapshot, sample);
            if (reason != null) {
                buffer = Datagrams.feedback(buffer, 1, snapshot);
                checksum += buffer.getLong(16);
                schedule.sent(snapshot.sampleNs(), sample);
                reportId++;
                pending = new RateController.Observation(snapshot, snapshot.echo() != null);
            }
            schedulingNs += System.nanoTime() - mark;
            if (tick % 2 == 0) {
                mark = System.nanoTime();
                var decision = controller.decide(sample, rate, pending);
                decisionNs += System.nanoTime() - mark;
                rate = decision.rate();
                pending = null;
                decisionCalls++;
            }
        }
        return new Counters(observationNs, schedulingNs, decisionNs, SAMPLES, reportId, decisionCalls,
            checksum + rate);
    }
}
