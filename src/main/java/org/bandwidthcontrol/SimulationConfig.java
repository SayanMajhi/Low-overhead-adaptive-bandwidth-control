package org.bandwidthcontrol;

import com.fasterxml.jackson.core.JsonParser;
import com.fasterxml.jackson.databind.DeserializationFeature;
import com.fasterxml.jackson.databind.MapperFeature;
import com.fasterxml.jackson.databind.SerializationFeature;
import com.fasterxml.jackson.databind.cfg.CoercionAction;
import com.fasterxml.jackson.databind.cfg.CoercionInputShape;
import com.fasterxml.jackson.databind.json.JsonMapper;
import com.fasterxml.jackson.databind.type.LogicalType;
import java.io.IOException;
import java.nio.ByteBuffer;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;

final class SimulationConfig {
    static final JsonMapper JSON = JsonMapper.builder()
        .enable(JsonParser.Feature.STRICT_DUPLICATE_DETECTION)
        .enable(DeserializationFeature.FAIL_ON_MISSING_CREATOR_PROPERTIES,
                DeserializationFeature.FAIL_ON_NULL_CREATOR_PROPERTIES,
                DeserializationFeature.FAIL_ON_NULL_FOR_PRIMITIVES,
                DeserializationFeature.FAIL_ON_TRAILING_TOKENS)
        .enable(SerializationFeature.ORDER_MAP_ENTRIES_BY_KEYS)
        .disable(DeserializationFeature.ACCEPT_FLOAT_AS_INT)
        .disable(MapperFeature.ALLOW_COERCION_OF_SCALARS)
        .withCoercionConfig(LogicalType.Textual, config -> config
            .setCoercion(CoercionInputShape.Integer, CoercionAction.Fail)
            .setCoercion(CoercionInputShape.Float, CoercionAction.Fail)
            .setCoercion(CoercionInputShape.Boolean, CoercionAction.Fail))
        .build();
    static final long NS_PER_MS = 1_000_000;

    record Media(int frameIntervalMs, int deadlineMs, int basePackets, int enhancementPackets,
                 int payloadBytesPerPacket) {}
    record Capacity(int atMs, int bps) {}
    record Blackout(int fromMs, int untilMs) {}
    record ExtraDelay(int fromMs, int untilMs, int addMs) {}
    record Background(int fromMs, int untilMs, int packetIpBytes, int intervalMs) {}
    record PathSettings(int queueBytes, List<Capacity> capacity, int delayMs, int jitterMs, int lossPpm,
                        int burstEnterPpm, int burstExitPpm, List<Blackout> blackouts, List<ExtraDelay> extraDelay) {}
    record Scenario(int schemaVersion, String scenarioId, int warmupMs, int measureMs, int drainMs, Media media,
                    PathSettings forward, PathSettings reverse, List<Background> background) {
        int stopMs() {
            return warmupMs + measureMs;
        }

        long drainNs() {
            return ns(stopMs() + drainMs);
        }
    }
    record Hybrid(int delayHighUs, int delayLowUs, int decreasePermille, int heartbeatMs,
                  int minEventSpacingMs, int enterSamples, int exitSamples, int matchedPeriodMs) {}
    record Profile(int schemaVersion, String profileId, int periodMs, int fixedBps, int initialBps, int minBps,
                   int maxBps, int minOutcomes, int highLossPermille, int lowLossPermille, int healthyDeliveryPermille,
                   int freshMs, int staleMs, int staleSpacingMs, int congestionSpacingMs, int maxGrowthCreditMs,
                   int additiveBpsPerSecond, int aimdDecreasePermille, int stalePermille) {
        ProfileWithHybrid withHybrid(Hybrid hybrid) {
            return new ProfileWithHybrid(this, hybrid);
        }
    }
    record ProfileWithHybrid(Profile base, Hybrid hybrid) {}
    record Input<T>(T value, byte[] bytes) {}

    private SimulationConfig() {}

    static Input<Scenario> scenario(Path path) throws IOException {
        byte[] bytes = Files.readAllBytes(path);
        Scenario value = read(path, bytes, Scenario.class);
        require(value.schemaVersion() == 1, "scenario schemaVersion must be 1");
        require(value.scenarioId().matches("[a-z0-9]+(?:-[a-z0-9]+)*"), "invalid scenarioId");
        require(value.media().equals(new Media(20, 150, 2, 8, 500)), "media does not match version 1");
        require(value.warmupMs() >= 0 && value.measureMs() > 0 && value.drainMs() >= 150
                && (long) value.warmupMs() + value.measureMs() + value.drainMs() <= 300_000,
                "invalid duration or total exceeds 300000 ms");
        require(value.warmupMs() % 20 == 0 && value.measureMs() % 20 == 0, "durations must align to frames");
        list(value.background());
        int backgroundMax = Datagrams.MEDIA_IP_BYTES;
        int end = 0;
        for (Background item : value.background()) {
            end = interval(item.fromMs(), item.untilMs(), end, value.stopMs());
            require(item.intervalMs() > 0 && item.packetIpBytes() >= 28 && item.packetIpBytes() <= 1500,
                    "invalid background packet length or interval");
            backgroundMax = Math.max(backgroundMax, item.packetIpBytes());
        }
        validatePath(value.forward(), value, backgroundMax);
        validatePath(value.reverse(), value, Datagrams.FEEDBACK_IP_BYTES);
        return new Input<>(value, bytes);
    }

    static Input<ProfileWithHybrid> profile(Path path) throws IOException {
        byte[] bytes = Files.readAllBytes(path);
        var root = read(path, bytes, com.fasterxml.jackson.databind.node.ObjectNode.class);
        var extension = root.remove("hybrid");
        Hybrid hybrid = extension == null ? null : convert(path, extension, Hybrid.class);
        require(extension == null || hybrid != null, "hybrid must be an object");
        Profile value = convert(path, root, Profile.class);
        require(value.schemaVersion() == 1 && value.profileId().matches("[a-z0-9]+(?:-[a-z0-9]+)*"),
                "invalid profile identity");
        require(value.periodMs() == 100 && value.fixedBps() == 1_400_000, "baseline period and fixed target are frozen");
        require(value.minBps() >= Sender.MIN_RATE && value.maxBps() <= Sender.MAX_RATE
                && value.initialBps() >= value.minBps() && value.initialBps() <= value.maxBps(),
                "invalid AIMD rates");
        require(value.minOutcomes() > 0 && value.minOutcomes() <= Datagrams.HISTORY_SIZE
                && value.lowLossPermille() >= 0 && value.highLossPermille() > value.lowLossPermille()
                && value.highLossPermille() <= 1000
                && value.healthyDeliveryPermille() > 0 && value.healthyDeliveryPermille() <= 1000,
                "invalid loss or utilization threshold");
        require(value.freshMs() > 0 && value.staleMs() > value.freshMs() && value.staleMs() <= 300_000
                && value.staleSpacingMs() > 0 && value.congestionSpacingMs() > 0
                && value.maxGrowthCreditMs() > 0 && value.maxGrowthCreditMs() <= 1000
                && value.additiveBpsPerSecond() > 0 && value.additiveBpsPerSecond() <= 10_000_000,
                "invalid rate timing or growth");
        require(value.aimdDecreasePermille() > 0 && value.aimdDecreasePermille() < 1000
                && value.stalePermille() > 0 && value.stalePermille() < 1000, "invalid decrease factor");
        if (hybrid != null) {
            require(hybrid.delayLowUs() >= 0 && hybrid.delayHighUs() > hybrid.delayLowUs()
                && hybrid.delayHighUs() <= 250_000, "invalid delay thresholds");
            require(hybrid.decreasePermille() > 0 && hybrid.decreasePermille() < 1000,
                    "invalid hybrid decrease factor");
            require(hybrid.heartbeatMs() >= 100 && hybrid.heartbeatMs() <= 500
                    && hybrid.heartbeatMs() % 50 == 0 && hybrid.minEventSpacingMs() == 100,
                    "heartbeat must be 100–500 ms in 50 ms steps and event spacing must be 100 ms");
            require(hybrid.enterSamples() > 0 && hybrid.enterSamples() <= 10
                && hybrid.exitSamples() > 0 && hybrid.exitSamples() <= 10, "invalid latch persistence");
            require(hybrid.matchedPeriodMs() >= 100 && hybrid.matchedPeriodMs() <= 500
                && hybrid.matchedPeriodMs() % 50 == 0, "matched period must be a sample multiple from 100 to 500 ms");
        }
        return new Input<>(value.withHybrid(hybrid), bytes);
    }

    private static <T> T convert(Path path, com.fasterxml.jackson.databind.JsonNode tree, Class<T> type) {
        try {
            return JSON.treeToValue(tree, type);
        } catch (IOException invalid) {
            throw new IllegalArgumentException(path + ": " + invalid.getMessage(), invalid);
        }
    }

    private static <T> T read(Path path, byte[] bytes, Class<T> type) throws IOException {
        try {
            String text = StandardCharsets.UTF_8.newDecoder().decode(ByteBuffer.wrap(bytes)).toString();
            T parsed = JSON.readValue(text, type);
            require(parsed != null, "missing JSON object");
            return parsed;
        } catch (IOException | IllegalArgumentException invalid) {
            throw new IllegalArgumentException(path + ": " + invalid.getMessage(), invalid);
        }
    }

    private static void validatePath(PathSettings path, Scenario scenario, int minQueue) {
        list(path.capacity());
        list(path.blackouts());
        list(path.extraDelay());
        require(path.queueBytes() >= minQueue, "directional queue smaller than largest packet");
        require(path.delayMs() >= 0 && path.jitterMs() >= 0 && path.jitterMs() <= path.delayMs()
                && (long) path.delayMs() + path.jitterMs() <= scenario.drainMs(), "invalid path delay or jitter");
        require(ppm(path.lossPpm()) && ppm(path.burstEnterPpm()) && ppm(path.burstExitPpm())
                && (path.burstEnterPpm() == 0 || path.burstExitPpm() > 0), "invalid impairment probability");
        require(!path.capacity().isEmpty() && path.capacity().getFirst().atMs() == 0, "capacity must start at zero");
        int previous = -1;
        for (Capacity capacity : path.capacity()) {
            require(capacity.atMs() > previous && capacity.atMs() < scenario.stopMs() && capacity.bps() > 0
                    && capacity.bps() <= 10_000_000, "invalid capacity transition or rate exceeds 10000000 bps");
            previous = capacity.atMs();
        }
        previous = 0;
        for (Blackout blackout : path.blackouts()) {
            previous = interval(blackout.fromMs(), blackout.untilMs(), previous, scenario.stopMs());
        }
        previous = 0;
        for (ExtraDelay delay : path.extraDelay()) {
            previous = interval(delay.fromMs(), delay.untilMs(), previous, scenario.stopMs());
            require(delay.addMs() >= 0 && (long) path.delayMs() + path.jitterMs() + delay.addMs() <= scenario.drainMs(),
                    "extra path delay exceeds drain");
        }
    }

    private static void list(List<?> items) {
        require(items.stream().noneMatch(java.util.Objects::isNull), "null list item");
    }

    private static int interval(int from, int until, int previous, int stop) {
        require(from >= previous && until > from && until <= stop, "invalid or overlapping interval");
        return until;
    }

    private static boolean ppm(int value) {
        return value >= 0 && value <= 1_000_000;
    }

    private static void require(boolean condition, String message) {
        if (!condition) {
            throw new IllegalArgumentException(message);
        }
    }

    static long ns(int ms) {
        return ms * NS_PER_MS;
    }
}
