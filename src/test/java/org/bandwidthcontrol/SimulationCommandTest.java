package org.bandwidthcontrol;

import static org.junit.jupiter.api.Assertions.assertArrayEquals;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

class SimulationCommandTest {
    private static final Path SCENARIO = Path.of("config/fixtures/baseline-smoke.json");
    private static final Path PROFILE = Path.of("config/profiles/baselines.json");

    @TempDir
    Path root;

    @Test
    void repeatedRunsMatchAndDifferentArmsShareTrace() throws Exception {
        Path first = run("aimd", "8341", "first");
        Path repeat = run("aimd", "8341", "repeat");
        Path fixed = run("fixed", "8341", "fixed");
        for (String artifact : List.of("sender.csv", "receiver.csv", "links.csv", "emission.csv", "summary.json")) {
            assertArrayEquals(Files.readAllBytes(first.resolve(artifact)), Files.readAllBytes(repeat.resolve(artifact)));
        }
        assertEquals(traceHash(first), traceHash(fixed));
        assertNotEquals(Files.readString(first.resolve("sender.csv")), Files.readString(fixed.resolve("sender.csv")));
        var value = SimulationConfig.scenario(SCENARIO).value();
        assertNotEquals(new ImpairmentTrace(value, 8342).hash(), traceHash(first));
        assertThrows(java.nio.file.FileAlreadyExistsException.class, () -> run("aimd", "8341", "first"));
    }

    @Test
    void strictParserRejectsDuplicateUnknownNonIntegralAndNullFields() throws Exception {
        String body = Files.readString(SCENARIO);
        List<String> invalid = List.of(
            body.replace("\"schemaVersion\": 1", "\"schemaVersion\": 1, \"schemaVersion\": 1"),
            body.replace("\"schemaVersion\": 1", "\"schemaVersion\": 1, \"unknown\": 1"),
            body.replace("\"schemaVersion\": 1", "\"schemaVersion\": 1.0"),
            body.replace("\"schemaVersion\": 1", "\"schemaVersion\": null"),
            body.replace("\"schemaVersion\": 1", "\"schemaVersion\": \"1\""),
            body.replace("\"scenarioId\": \"baseline-smoke\"", "\"scenarioId\": 7"),
            body.replace("\"queueBytes\": 3000", "\"queueBytes\": 999"));
        for (int i = 0; i < invalid.size(); i++) {
            Path path = root.resolve("invalid-" + i + ".json");
            Files.writeString(path, invalid.get(i));
            assertThrows(IllegalArgumentException.class, () -> SimulationConfig.scenario(path), invalid.get(i));
        }
    }

    private Path run(String arm, String seed, String name) throws Exception {
        Path output = root.resolve(name);
        SimulationCommand.run(new String[]{"simulate", "--scenario", SCENARIO.toString(), "--profile",
            PROFILE.toString(), "--arm", arm, "--seed", seed, "--out", output.toString()});
        return output;
    }

    private String traceHash(Path output) throws Exception {
        return SimulationConfig.JSON.readTree(output.resolve("manifest.json").toFile()).get("exogenousTraceSha256").textValue();
    }
}
