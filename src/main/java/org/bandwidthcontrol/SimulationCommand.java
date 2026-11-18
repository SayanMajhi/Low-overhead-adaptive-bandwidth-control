package org.bandwidthcontrol;

import java.io.IOException;
import java.lang.management.ManagementFactory;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Instant;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;

final class SimulationCommand {
    private SimulationCommand() {}

    static void run(String[] args) throws IOException {
        Instant start = Instant.now();
        Map<String, String> options = parse(args);
        String arm = options.get("--arm");
        SimulationArm mode = SimulationArm.parse(arm);
        long seed = Long.parseLong(options.get("--seed"));
        var scenario = SimulationConfig.scenario(Path.of(options.get("--scenario")));
        var profile = SimulationConfig.profile(Path.of(options.get("--profile")));
        mode.validate(profile.value());
        var trace = new ImpairmentTrace(scenario.value(), seed);
        Path output = Path.of(options.get("--out"));
        Files.createDirectory(output);
        Path senderCsv = output.resolve("sender.csv");
        Path receiverCsv = output.resolve("receiver.csv");
        String scenarioHash = ImpairmentTrace.sha256(scenario.bytes());
        String profileHash = ImpairmentTrace.sha256(profile.bytes());
        String runId = scenario.value().scenarioId() + "-" + arm + "-" + seed + "-"
            + scenarioHash.substring(0, 8) + "-" + profileHash.substring(0, 8);
        writeJson(output.resolve("manifest.json"), Map.ofEntries(
            Map.entry("schemaVersion", 1),
            Map.entry("runId", runId),
            Map.entry("arm", arm),
            Map.entry("seed", seed),
            Map.entry("scenarioSha256", scenarioHash),
            Map.entry("profileSha256", profileHash),
            Map.entry("exogenousTraceSha256", trace.hash()),
            Map.entry("clock", "virtual_nanoseconds"),
            Map.entry("startUtc", start.toString()),
            Map.entry("javaVersion", System.getProperty("java.runtime.version")),
            Map.entry("javaVendor", System.getProperty("java.vendor")),
            Map.entry("vmArguments", ManagementFactory.getRuntimeMXBean().getInputArguments()),
            Map.entry("host", System.getProperty("os.name") + " " + System.getProperty("os.version")
                + " " + System.getProperty("os.arch")),
            Map.entry("buildSha256", buildHash()),
            Map.entry("arguments", List.of(args)),
            Map.entry("workingDirectory", Path.of("").toAbsolutePath().toString())
        ));
        Files.write(output.resolve("scenario.json"), scenario.bytes());
        Files.write(output.resolve("profile.json"), profile.bytes());
        var metrics = new Metrics(scenario.value());
        try (var send = new Events(senderCsv, 1); var receive = new Events(receiverCsv, 1);
             var net = Files.newBufferedWriter(output.resolve("links.csv"))) {
            new Simulation(scenario.value(), profile.value(), mode, trace, send, receive, net, metrics).run();
        }
        writeJson(output.resolve("summary.json"), metrics.summarize(senderCsv, receiverCsv, output.resolve("emission.csv")));
        System.out.println("DONE simulate " + runId + " " + output);
    }

    private static Map<String, String> parse(String[] args) {
        Set<String> keys = Set.of("--scenario", "--profile", "--arm", "--seed", "--out");
        Map<String, String> options = new HashMap<>();
        for (int index = 1; index < args.length; index += 2) {
            String key = args[index];
            if (index + 1 == args.length || !keys.contains(key) || options.putIfAbsent(key, args[index + 1]) != null) {
                throw new IllegalArgumentException("invalid or repeated simulation option: " + key);
            }
        }
        if (!options.keySet().equals(keys)) {
            throw new IllegalArgumentException("simulate requires --scenario, --profile, --arm, --seed and --out");
        }
        return options;
    }

    private static String buildHash() throws IOException {
        var location = SimulationCommand.class.getProtectionDomain().getCodeSource().getLocation();
        Path path;
        try {
            path = Path.of(location.toURI());
        } catch (java.net.URISyntaxException invalid) {
            throw new IOException("cannot identify executable build", invalid);
        }
        return Files.isRegularFile(path) ? ImpairmentTrace.sha256(Files.readAllBytes(path)) : "exploded_classes";
    }

    private static void writeJson(Path path, Object data) throws IOException {
        SimulationConfig.JSON.writerWithDefaultPrettyPrinter().writeValue(path.toFile(), data);
    }
}
