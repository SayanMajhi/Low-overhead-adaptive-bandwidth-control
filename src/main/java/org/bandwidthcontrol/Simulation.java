package org.bandwidthcontrol;

import static org.bandwidthcontrol.SimulationConfig.ns;

import java.io.BufferedWriter;
import java.io.IOException;
import java.nio.ByteBuffer;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Comparator;
import java.util.PriorityQueue;

final class Simulation {
    private record Arrival(long time, long ordinal, SimulatedLink.Packet packet) {}

    private final SimulationConfig.Scenario scenario;
    private final ImpairmentTrace trace;
    private final Sender sender;
    private final Receiver receiver;
    private final SimulatedLink forward;
    private final SimulatedLink reverse;
    private final BufferedWriter linkLog;
    private final Metrics metrics;
    private final PriorityQueue<Arrival> arrivals = new PriorityQueue<>(
        Comparator.comparingLong(Arrival::time).thenComparingLong(Arrival::ordinal));
    private final long stop;
    private final long cutoff;
    private long now;
    private long ordinal;
    private long backgroundId;
    private long mediaId;
    private long reportId;
    private int backgroundIndex;
    private long nextBackground;
    private int forwardStep = 1;
    private int reverseStep = 1;
    private SimulatedLink.Packet pendingMedia;
    private SimulatedLink.Packet pendingFeedback;

    Simulation(SimulationConfig.Scenario scenario, SimulationConfig.ProfileWithHybrid profile, SimulationArm arm,
               ImpairmentTrace trace, Events sendLog, Events receiveLog, BufferedWriter linkLog, Metrics metrics) {
        this.scenario = scenario;
        this.trace = trace;
        this.linkLog = linkLog;
        this.metrics = metrics;
        sender = new Sender(1, arm.startRate(profile.base()), sendLog, arm.controller(profile));
        receiver = new Receiver(1, receiveLog, arm.feedback(profile));
        stop = ns(scenario.stopMs());
        cutoff = scenario.drainNs();
        nextBackground = scenario.background().isEmpty() ? SimulatedLink.NONE
            : ns(scenario.background().getFirst().fromMs());
        forward = link(true, scenario.forward());
        reverse = link(false, scenario.reverse());
    }

    void run() throws IOException {
        linkLog.write("elapsed_ns,direction,event,stream,packet_id,ip_bytes,waiting_ip_bytes,reason,service_from_ns,service_work_bit_ns,service_capacity_bps\n");
        while (true) {
            forward.advance(now);
            reverse.advance(now);
            if (now == capacityTime(scenario.forward(), forwardStep)) {
                forward.rate(scenario.forward().capacity().get(forwardStep++).bps());
            }
            if (now == capacityTime(scenario.reverse(), reverseStep)) {
                reverse.rate(scenario.reverse().capacity().get(reverseStep++).bps());
            }
            deliver();
            if (now < stop) {
                receiver.service(this::feedback, () -> now, now, stop);
                sender.service(this::media, () -> now, now, stop);
                emitBackground();
                admitEndpoints();
            } else {
                receiver.drain(now);
                if (now == stop) {
                    sender.close(now, stop);
                }
            }
            if (now == cutoff) {
                forward.cutoff(now);
                reverse.cutoff(now);
                return;
            }
            now = nextTime();
        }
    }

    private long nextTime() {
        long next = Math.min(cutoff, Math.min(forward.nextCompletion(), reverse.nextCompletion()));
        if (!arrivals.isEmpty()) {
            next = Math.min(next, arrivals.peek().time());
        }
        next = Math.min(next, capacityTime(scenario.forward(), forwardStep));
        next = Math.min(next, capacityTime(scenario.reverse(), reverseStep));
        if (now < stop) {
            next = Math.min(next, Math.min(stop, Math.min(sender.nextWake(), receiver.nextWake())));
            next = Math.min(next, nextBackground);
        }
        if (next <= now) {
            throw new IllegalStateException("virtual time stalled at " + now + " ns");
        }
        return next;
    }

    private long capacityTime(SimulationConfig.PathSettings path, int index) {
        return index == path.capacity().size() ? SimulatedLink.NONE : ns(path.capacity().get(index).atMs());
    }

    private void deliver() throws IOException {
        while (!arrivals.isEmpty() && arrivals.peek().time() == now) {
            var packet = arrivals.remove().packet();
            writeLink(now, packet.stream().equals("feedback") ? "reverse" : "forward", "arrived", packet, "", "",
                      "", "");
            if (packet.stream().equals("media")) {
                receiver.receive(ByteBuffer.wrap(packet.data()), now);
            } else if (packet.stream().equals("feedback")) {
                sender.receive(ByteBuffer.wrap(packet.data()), now);
            } else {
                metrics.backgroundArrived(packet.id(), packet.bytes());
            }
        }
    }

    private int media(ByteBuffer data) {
        pendingMedia = copy("media", mediaId++, data);
        return pendingMedia.data().length;
    }

    private int feedback(ByteBuffer data) {
        pendingFeedback = copy("feedback", reportId++, data);
        return pendingFeedback.data().length;
    }

    private SimulatedLink.Packet copy(String stream, long id, ByteBuffer data) {
        byte[] bytes = new byte[data.remaining()];
        data.get(bytes);
        return new SimulatedLink.Packet(stream, id, bytes.length + Datagrams.UDP_IP_BYTES, bytes);
    }

    private void emitBackground() throws IOException {
        if (now != nextBackground) {
            return;
        }
        var plan = scenario.background().get(backgroundIndex);
        var packet = new SimulatedLink.Packet("background", backgroundId++, plan.packetIpBytes(), null);
        metrics.backgroundSent(packet.id(), backgroundIndex);
        writeLink(now, "forward", "emitted", packet, "", "", "", "");
        forward.offer(now, packet);
        nextBackground = now + ns(plan.intervalMs());
        if (nextBackground >= ns(plan.untilMs())) {
            backgroundIndex++;
            nextBackground = backgroundIndex == scenario.background().size()
                ? SimulatedLink.NONE : ns(scenario.background().get(backgroundIndex).fromMs());
        }
    }

    private void admitEndpoints() throws IOException {
        if (pendingMedia != null) {
            forward.offer(now, pendingMedia);
            pendingMedia = null;
        }
        if (pendingFeedback != null) {
            reverse.offer(now, pendingFeedback);
            pendingFeedback = null;
        }
    }

    private SimulatedLink link(boolean isForward, SimulationConfig.PathSettings path) {
        String direction = isForward ? "forward" : "reverse";
        return new SimulatedLink(path.capacity().getFirst().bps(), path.queueBytes(), new SimulatedLink.Observer() {
            public void event(long time, String type, SimulatedLink.Packet packet, int waiting, String reason)
                    throws IOException {
                writeLink(time, direction, type, packet, Integer.toString(waiting), reason, "", "");
                if (type.equals("drop") || type.equals("undelivered")) {
                    metrics.pathOutcome(packet.stream(), packet.id(), reason);
                }
            }

            public void service(SimulatedLink.Service service) throws IOException {
                if (isForward) {
                    metrics.service(service);
                }
                var packet = service.packet();
                linkLog.write(service.until() + "," + direction + ",service," + packet.stream() + ","
                              + packet.id() + "," + packet.bytes() + ",,," + service.from() + ","
                              + service.work() + "," + service.rate() + "\n");
            }

            public void complete(long time, SimulatedLink.Packet packet) throws IOException {
                var state = trace.state(isForward, path, time);
                if (state.dropReason() != null) {
                    writeLink(time, direction, "drop", packet, "", state.dropReason(), "", "");
                    metrics.pathOutcome(packet.stream(), packet.id(), state.dropReason());
                } else {
                    long arrival = time + state.propagationNs();
                    if (arrival <= cutoff) {
                        arrivals.add(new Arrival(arrival, ordinal++, packet));
                    } else {
                        writeLink(time, direction, "undelivered", packet, "", "propagation_after_cutoff", "", "");
                        metrics.pathOutcome(packet.stream(), packet.id(), "propagation_after_cutoff");
                    }
                }
            }
        });
    }

    private void writeLink(long time, String direction, String event, SimulatedLink.Packet packet,
                           String waiting, String reason, String serviceFrom, String serviceWork) throws IOException {
        linkLog.write(time + "," + direction + "," + event + "," + packet.stream() + "," + packet.id() + ","
                      + packet.bytes() + "," + waiting + "," + reason + "," + serviceFrom + "," + serviceWork + ",\n");
    }
}
