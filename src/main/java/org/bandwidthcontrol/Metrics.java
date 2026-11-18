package org.bandwidthcontrol;

import java.io.BufferedReader;
import java.io.BufferedWriter;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.TreeMap;

import static org.bandwidthcontrol.SimulationConfig.ns;

final class Metrics {
    private record Send(long now, int frame, int index) {}
    private record Arrival(long now, long sequence, String reason) {}
    private static final class Frame {
        int base;
        int onTimeBytes;
    }
    private static final class Background {
        long sentPackets;
        long receivedPackets;
        long receivedBytes;
    }

    private final SimulationConfig.Scenario scenario;
    private final long start;
    private final long stop;
    private final Map<Long, String> mediaOutcomes = new HashMap<>();
    private final Map<Long, Integer> backgroundIntervals = new HashMap<>();
    private final Map<Integer, Background> backgrounds = new TreeMap<>();
    private long totalService;
    private long mediaService;

    Metrics(SimulationConfig.Scenario scenario) {
        this.scenario = scenario;
        start = ns(scenario.warmupMs());
        stop = ns(scenario.stopMs());
    }

    void backgroundSent(long id, int interval) {
        backgroundIntervals.put(id, interval);
        backgrounds.computeIfAbsent(interval, ignored -> new Background()).sentPackets++;
    }

    void backgroundArrived(long id, int bytes) {
        var group = backgrounds.get(backgroundIntervals.get(id));
        group.receivedPackets++;
        group.receivedBytes += bytes;
    }

    void pathOutcome(String stream, long id, String reason) {
        if (stream.equals("media")) {
            mediaOutcomes.put(id, reason);
        }
    }

    void service(SimulatedLink.Service service) {
        long low = Math.max(start, service.from());
        long high = Math.min(stop, service.until());
        if (low >= high) {
            return;
        }
        long work = Math.min(service.work(), (high - service.from()) * service.rate())
                  - Math.min(service.work(), (low - service.from()) * service.rate());
        totalService += work;
        if (service.packet().stream().equals("media")) {
            mediaService += work;
        }
    }

    Map<String, Object> summarize(Path sendPath, Path receivePath, Path binsPath) throws IOException {
        var sourceFrames = new HashMap<Integer, Frame>();
        var sends = new HashMap<Long, Send>();
        var arrivals = new ArrayList<Arrival>();
        var rates = new ArrayList<Integer>();
        long[] emission = new long[Math.ceilDiv(scenario.measureMs(), 1000)];
        long sentIpBytes = 0;
        long baseDiscardBytes = 0;
        long enhancementDiscardBytes = 0;
        long feedbackPayload = 0;
        long feedbackIp = 0;
        long feedbackPackets = 0;
        long feedbackReceived = 0;
        long duplicates = 0;
        try (BufferedReader log = Files.newBufferedReader(sendPath)) {
            log.readLine();
            String line;
            while ((line = log.readLine()) != null) {
                String[] row = line.split(",", -1);
                long now = Long.parseLong(row[0]);
                switch (row[1]) {
                    case "frame_created" -> {
                        if (inWindow(now)) {
                            sourceFrames.put(Integer.parseInt(row[4]), new Frame());
                        }
                    }
                    case "media_sent" -> {
                        int frame = Integer.parseInt(row[4]);
                        if (sourceFrames.containsKey(frame)) {
                            sends.put(Long.parseLong(row[3]), new Send(now, frame, Integer.parseInt(row[5])));
                        }
                        if (inWindow(now)) {
                            long bytes = Long.parseLong(row[9]);
                            sentIpBytes += bytes;
                            emission[(int) ((now - start) / 1_000_000_000L)] += bytes;
                        }
                    }
                    case "source_discard" -> {
                        if (sourceFrames.containsKey(Integer.parseInt(row[4]))) {
                            if (Integer.parseInt(row[5]) < 2) {
                                baseDiscardBytes += Datagrams.PAYLOAD_BYTES;
                            } else {
                                enhancementDiscardBytes += Datagrams.PAYLOAD_BYTES;
                            }
                        }
                    }
                    case "feedback_received" -> {
                        if (inWindow(now)) {
                            feedbackReceived++;
                        }
                    }
                    case "rate_tick" -> {
                        if (inWindow(now)) {
                            rates.add(Integer.parseInt(row[7]));
                        }
                    }
                    default -> {}
                }
            }
        }
        try (BufferedReader log = Files.newBufferedReader(receivePath)) {
            log.readLine();
            String line;
            while ((line = log.readLine()) != null) {
                String[] row = line.split(",", -1);
                long now = Long.parseLong(row[0]);
                if (row[1].equals("feedback_sent") && inWindow(now)) {
                    feedbackPackets++;
                    feedbackPayload += Long.parseLong(row[8]);
                    feedbackIp += Long.parseLong(row[9]);
                } else if (row[1].equals("media_received") && sends.containsKey(Long.parseLong(row[3]))) {
                    arrivals.add(new Arrival(now, Long.parseLong(row[3]), row[10]));
                } else if (row[1].equals("media_duplicate") && sends.containsKey(Long.parseLong(row[3]))) {
                    duplicates++;
                }
            }
        }
        arrivals.sort(Comparator.comparingLong(Arrival::now).thenComparingLong(Arrival::sequence));
        var delays = new ArrayList<Long>();
        long late = 0;
        long reorder = 0;
        long jitterSum = 0;
        long lastTransit = -1;
        long onTimeBytes = 0;
        for (Arrival arrival : arrivals) {
            Send send = sends.remove(arrival.sequence());
            if (send == null) {
                continue;
            }
            long transit = arrival.now() - send.now();
            if (lastTransit >= 0) {
                jitterSum += Math.abs(transit - lastTransit);
            }
            lastTransit = transit;
            delays.add(transit);
            if (!arrival.reason().equals("in_order")) {
                reorder++;
            }
            long deadline = ns(send.frame() * scenario.media().frameIntervalMs() + scenario.media().deadlineMs());
            if (arrival.now() <= deadline) {
                onTimeBytes += Datagrams.PAYLOAD_BYTES;
                var frame = sourceFrames.get(send.frame());
                frame.onTimeBytes += Datagrams.PAYLOAD_BYTES;
                if (send.index() < 2) {
                    frame.base++;
                }
            } else {
                late++;
            }
        }

        long completed = 0;
        long decodable = 0;
        for (Frame frame : sourceFrames.values()) {
            if (frame.base == 2) {
                completed++;
                decodable += frame.onTimeBytes;
            }
        }
        Map<String, Long> losses = new TreeMap<>();
        for (long id : sends.keySet()) {
            losses.merge(mediaOutcomes.getOrDefault(id, "receiver_rejected"), 1L, Long::sum);
        }
        delays.sort(null);
        long emitted = sends.size() + delays.size();
        long capacity = capacityWork();
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("frames_generated", sourceFrames.size());
        result.put("base_frames_complete", completed);
        result.put("base_completion_ratio", ratio(completed, sourceFrames.size()));
        result.put("base_deadline_misses", sourceFrames.size() - completed);
        result.put("source_discard_base_payload_bytes", baseDiscardBytes);
        result.put("source_discard_enhancement_payload_bytes", enhancementDiscardBytes);
        result.put("delivered_payload_bps", bps((long) delays.size() * Datagrams.PAYLOAD_BYTES));
        result.put("on_time_payload_goodput_bps", bps(onTimeBytes));
        result.put("decodable_proxy_goodput_bps", bps(decodable));
        result.put("media_emitted_measured_frames", emitted);
        result.put("media_emitted_in_measurement_ip_bytes", sentIpBytes);
        result.put("media_delivery_samples", delays.size());
        result.put("media_losses", sends.size());
        result.put("media_loss_ratio", ratio(sends.size(), emitted));
        result.put("losses_by_cause", losses);
        result.put("media_duplicate_arrivals", duplicates);
        result.put("media_late_arrivals", late);
        result.put("media_reordered_arrivals", reorder);
        result.put("packet_median_delay_ns", quantile(delays, 50));
        result.put("packet_p95_delay_ns", quantile(delays, 95));
        result.put("interarrival_jitter_ms", delays.size() < 2 ? null : jitterSum / ((delays.size() - 1) * 1_000_000.0));
        result.put("feedback_packets_emitted", feedbackPackets);
        result.put("feedback_packets_received_in_measurement", feedbackReceived);
        result.put("feedback_application_bytes", feedbackPayload);
        result.put("feedback_ip_bytes", feedbackIp);
        result.put("feedback_ip_bps", bps(feedbackIp));
        result.put("feedback_traffic_share", ratio(feedbackIp, sentIpBytes + feedbackIp));
        result.put("forward_capacity_work_bit_ns", capacity);
        result.put("forward_service_work_bit_ns", totalService);
        result.put("forward_media_service_work_bit_ns", mediaService);
        result.put("forward_utilization", ratio(totalService, capacity));
        result.put("forward_media_utilization", ratio(mediaService, capacity));
        result.put("rate_variability", rateStats(rates));
        result.put("background_intervals", backgroundStats());
        writeBins(binsPath, emission);
        return result;
    }

    private boolean inWindow(long now) {
        return start <= now && now < stop;
    }

    private long capacityWork() {
        var path = scenario.forward().capacity();
        long work = 0;
        for (int i = 0; i < path.size(); i++) {
            long from = Math.max(start, ns(path.get(i).atMs()));
            long until = i + 1 < path.size() ? Math.min(stop, ns(path.get(i + 1).atMs())) : stop;
            if (until > from) {
                work += (until - from) * path.get(i).bps();
            }
        }
        return work;
    }

    private Map<Integer, Map<String, Object>> backgroundStats() {
        var result = new TreeMap<Integer, Map<String, Object>>();
        for (var entry : backgrounds.entrySet()) {
            var plan = scenario.background().get(entry.getKey());
            var sample = entry.getValue();
            result.put(entry.getKey(), Map.of("from_ms", plan.fromMs(), "until_ms", plan.untilMs(),
                "offered_packets", sample.sentPackets, "delivered_by_drain_packets", sample.receivedPackets,
                "loss_ratio", (sample.sentPackets - sample.receivedPackets) / (double) sample.sentPackets,
                "delivered_ip_bps_by_emission_interval",
                sample.receivedBytes * 8000.0 / (plan.untilMs() - plan.fromMs())));
        }
        return result;
    }

    private Map<String, Object> rateStats(ArrayList<Integer> samples) {
        int changes = 0;
        long totalChange = 0;
        double mean = samples.stream().mapToInt(Integer::intValue).average().orElse(0);
        double sumSquared = 0;
        for (int i = 0; i < samples.size(); i++) {
            int target = samples.get(i);
            sumSquared += (target - mean) * (target - mean);
            if (i > 0 && target != samples.get(i - 1)) {
                changes++;
                totalChange += Math.abs(target - samples.get(i - 1));
            }
        }
        double std = samples.isEmpty() ? 0 : Math.sqrt(sumSquared / samples.size());
        var stats = new LinkedHashMap<String, Object>();
        stats.put("sample_count", samples.size());
        stats.put("changes", changes);
        stats.put("mean_abs_change_bps", samples.size() < 2 ? null : totalChange / (double) (samples.size() - 1));
        stats.put("mean_bps", samples.isEmpty() ? null : mean);
        stats.put("std_bps", samples.isEmpty() ? null : std);
        stats.put("coefficient_of_variation", mean == 0 ? null : std / mean);
        return stats;
    }

    private void writeBins(Path path, long[] emission) throws IOException {
        try (BufferedWriter output = Files.newBufferedWriter(path)) {
            output.write("from_ns,until_ns,media_ip_bytes,media_ip_bps\n");
            for (int i = 0; i < emission.length; i++) {
                long from = start + i * 1_000_000_000L;
                long until = Math.min(stop, from + 1_000_000_000L);
                output.write(from + "," + until + "," + emission[i] + ","
                             + (emission[i] * 8_000_000_000.0 / (until - from)) + "\n");
            }
        }
    }

    private double bps(long bytes) {
        return bytes * 8000.0 / scenario.measureMs();
    }

    private static Double ratio(long numerator, long denominator) {
        return denominator == 0 ? null : numerator / (double) denominator;
    }

    private static Long quantile(ArrayList<Long> sorted, int percent) {
        return sorted.isEmpty() ? null : sorted.get(Math.ceilDiv(sorted.size() * percent, 100) - 1);
    }
}
