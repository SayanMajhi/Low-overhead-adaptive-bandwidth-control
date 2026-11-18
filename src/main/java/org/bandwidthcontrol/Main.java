package org.bandwidthcontrol;

import java.io.IOException;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.net.PortUnreachableException;
import java.net.StandardProtocolFamily;
import java.nio.ByteBuffer;
import java.nio.channels.DatagramChannel;
import java.nio.channels.SelectionKey;
import java.nio.channels.Selector;
import java.nio.file.Path;
import java.util.HashMap;
import java.util.HashSet;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.locks.LockSupport;

public final class Main {
    private static final String USAGE = """
            Usage: java -jar target/bandwidth-control.jar send|receive
              --bind IPv4:port --peer IPv4:port --session positive-u63 --run-ms 1..3600000
              --events path
              Fixed mode: --rate-bps 500000..2272000 for send
              Hybrid mode: --arm periodic|adaptive --profile path on both endpoints
            Usage: java -jar target/bandwidth-control.jar simulate
              --scenario path --profile path --arm name --seed integer --out new-directory
              Arms: fixed, aimd, periodic, adaptive, heartbeat-only, matched-budget, no-delay-periodic
            """;
    private static final int MAX_READS_PER_TURN = 64;

    private record Options(boolean send, InetSocketAddress bind, InetSocketAddress peer, long session,
                           long durationNs, Path events, int rate, SimulationArm arm,
                           SimulationConfig.ProfileWithHybrid profile) {
        static Options parse(String[] args) throws IOException {
            if (args.length == 0 || (!args[0].equals("send") && !args[0].equals("receive"))) {
                throw new IllegalArgumentException("choose send or receive");
            }
            boolean send = args[0].equals("send");
            Set<String> required = Set.of("--bind", "--peer", "--session", "--run-ms", "--events");
            Set<String> keys = new HashSet<>(required);
            keys.addAll(Set.of("--arm", "--profile"));
            if (send) {
                keys.add("--rate-bps");
            }
            Map<String, String> values = new HashMap<>();
            for (int i = 1; i < args.length; i += 2) {
                String key = args[i];
                if (!keys.contains(key) || i + 1 == args.length || values.putIfAbsent(key, args[i + 1]) != null) {
                    throw new IllegalArgumentException("unexpected, repeated, or missing value for " + key);
                }
            }
            if (!values.keySet().containsAll(required)) {
                throw new IllegalArgumentException("missing required endpoint options");
            }
            int runMs = (int) number("--run-ms", values.get("--run-ms"), 1, 3_600_000);
            long session = number("--session", values.get("--session"), 1, Long.MAX_VALUE);
            SimulationArm arm = null;
            SimulationConfig.ProfileWithHybrid profile = null;
            int rate = 0;
            if (values.containsKey("--arm") || values.containsKey("--profile")) {
                String armId = values.get("--arm");
                if (!Set.of("periodic", "adaptive").contains(armId == null ? "" : armId)
                    || !values.containsKey("--profile") || values.containsKey("--rate-bps")) {
                    throw new IllegalArgumentException("hybrid mode requires --arm periodic|adaptive and --profile, without --rate-bps");
                }
                arm = SimulationArm.parse(armId);
                profile = SimulationConfig.profile(Path.of(values.get("--profile"))).value();
                arm.validate(profile);
                rate = arm.startRate(profile.base());
            } else if (send) {
                rate = (int) number("--rate-bps", values.get("--rate-bps"), Sender.MIN_RATE, Sender.MAX_RATE);
            }
            if (values.get("--events").isBlank()) {
                throw new IllegalArgumentException("--events needs an output path");
            }
            return new Options(send, address("--bind", values), address("--peer", values), session,
                               runMs * 1_000_000L, Path.of(values.get("--events")), rate, arm, profile);
        }

        private static InetSocketAddress address(String key, Map<String, String> values) throws IOException {
            String[] parts = values.get(key).split(":", -1);
            if (parts.length != 2 || !parts[0].matches("[0-9]{1,3}(\\.[0-9]{1,3}){3}")) {
                throw new IllegalArgumentException(key + " needs a numeric IPv4 address and port");
            }
            byte[] octets = new byte[4];
            String[] segments = parts[0].split("\\.");
            for (int i = 0; i < octets.length; i++) {
                octets[i] = (byte) number(key + " octet", segments[i], 0, 255);
            }
            int port = (int) number(key + " port", parts[1], 1, 65_535);
            return new InetSocketAddress(InetAddress.getByAddress(octets), port);
        }

        private static long number(String key, String raw, long minimum, long maximum) {
            long value;
            try {
                value = Long.parseLong(raw);
            } catch (NumberFormatException invalid) {
                throw new IllegalArgumentException(key + " needs a decimal integer");
            }
            if (value < minimum || value > maximum) {
                throw new IllegalArgumentException(key + " must be between " + minimum + " and " + maximum);
            }
            return value;
        }
    }

    private Main() {}

    public static void main(String[] args) {
        if (args.length == 1 && args[0].equals("--help")) {
            System.out.print(USAGE);
            return;
        }
        try {
            if (args.length > 0 && args[0].equals("simulate")) {
                SimulationCommand.run(args);
            } else {
                run(Options.parse(args));
            }
        } catch (IllegalArgumentException invalid) {
            System.err.println(invalid.getMessage());
            System.err.print(USAGE);
            System.exit(2);
        } catch (IOException failure) {
            System.err.println("I/O error: " + failure.getMessage());
            System.exit(1);
        }
    }

    private static void run(Options options) throws IOException {
        try (DatagramChannel channel = DatagramChannel.open(StandardProtocolFamily.INET);
             Selector selector = Selector.open()) {
            channel.configureBlocking(false);
            channel.bind(options.bind());
            long start = System.nanoTime();
            channel.connect(options.peer());
            channel.register(selector, SelectionKey.OP_READ);
            try (Events events = new Events(options.events(), options.session())) {
                var sender = options.send()
                    ? new Sender(options.session(), options.rate(), events,
                        options.arm() == null ? null : options.arm().controller(options.profile()))
                    : null;
                var receiver = options.send() ? null
                    : options.arm() == null ? new Receiver(options.session(), events)
                    : new Receiver(options.session(), events, options.arm().feedback(options.profile()));
                String mode = options.send() ? "send" : "receive";
                System.out.println("READY " + mode + " " + options.bind());
                long end = options.durationNs();
                try {
                    loop(channel, selector, events, sender, receiver, start, end);
                } catch (IOException failure) {
                    try {
                        events.note(System.nanoTime() - start, "io_failure", "fatal");
                    } catch (IOException suppressed) {
                        failure.addSuppressed(suppressed);
                    }
                    throw failure;
                } finally {
                    if (sender != null) {
                        sender.close(System.nanoTime() - start, end);
                    }
                }
                events.note(System.nanoTime() - start, "run_ended", "duration");
                if (sender != null) {
                    System.out.printf("DONE send frames=%d media_sent=%d%n", sender.frames(), sender.sent());
                } else {
                    System.out.printf("DONE receive media_received=%d feedback_sent=%d%n",
                                      receiver.received(), receiver.reported());
                }
            }
        }
    }

    private static void loop(DatagramChannel channel, Selector selector, Events events, Sender sender,
                             Receiver receiver, long start, long end) throws IOException {
        ByteBuffer buffer = ByteBuffer.allocate(Datagrams.MEDIA_BYTES + 1);
        long now;
        while ((now = System.nanoTime() - start) < end) {
            for (int read = 0; read < MAX_READS_PER_TURN; read++) {
                buffer.clear();
                try {
                    if (channel.receive(buffer) == null) {
                        break;
                    }
                } catch (PortUnreachableException unreachable) {
                    events.note(System.nanoTime() - start, "peer_unreachable", "icmp");
                    break;
                }
                now = System.nanoTime() - start;
                if (now >= end) {
                    return;
                }
                buffer.flip();
                if (sender != null) {
                    sender.receive(buffer, now);
                } else {
                    receiver.receive(buffer, now);
                }
            }
            now = System.nanoTime() - start;
            if (now >= end) {
                return;
            }
            long wake;
            if (sender != null) {
                sender.service(channel::write, () -> System.nanoTime() - start, now, end);
                wake = sender.nextWake();
            } else {
                receiver.service(channel::write, () -> System.nanoTime() - start, now, end);
                wake = receiver.nextWake();
            }
            long wait = Math.min(wake, end) - (System.nanoTime() - start);
            if (wait >= 1_000_000) {
                selector.select(wait / 1_000_000);
                selector.selectedKeys().clear();
            } else if (wait > 0) {
                LockSupport.parkNanos(wait);
            }
        }
    }
}
