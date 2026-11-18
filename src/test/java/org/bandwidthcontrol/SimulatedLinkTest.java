package org.bandwidthcontrol;

import org.junit.jupiter.api.Test;
import java.util.ArrayList;
import java.util.List;
import static org.junit.jupiter.api.Assertions.assertEquals;

class SimulatedLinkTest {
    @Test
    void fifoSerializesAtRateAndDropsOnlyWaitingOverflow() throws Exception {
        var log = new Log();
        var link = new SimulatedLink(800_000, 1000, log);
        link.offer(0, packet(0, 1000));
        link.offer(0, packet(1, 1000));
        link.offer(0, packet(2, 1));
        assertEquals(10_000_000, link.nextCompletion());
        link.advance(10_000_000);
        assertEquals(20_000_000, link.nextCompletion());
        link.advance(20_000_000);
        assertEquals(List.of(2L), log.dropped);
        assertEquals(List.of(0L, 1L), log.completed);
        assertEquals(16_000_000_000_000L, log.work);
    }

    @Test
    void capacityStepPreservesPartialService() throws Exception {
        var log = new Log();
        var link = new SimulatedLink(800_000, 1000, log);
        link.offer(0, packet(0, 1000));
        link.advance(5_000_000);
        link.rate(400_000);
        assertEquals(15_000_000, link.nextCompletion());
        link.advance(15_000_000);
        assertEquals(List.of(0L), log.completed);
        assertEquals(8_000_000_000_000L, log.work);
    }

    private SimulatedLink.Packet packet(long id, int bytes) {
        return new SimulatedLink.Packet("background", id, bytes, null);
    }

    private static final class Log implements SimulatedLink.Observer {
        final List<Long> dropped = new ArrayList<>();
        final List<Long> completed = new ArrayList<>();
        long work;

        public void event(long now, String event, SimulatedLink.Packet packet, int waiting, String reason) {
            if (event.equals("drop")) {
                dropped.add(packet.id());
            }
        }

        public void service(SimulatedLink.Service service) {
            work += service.work();
        }

        public void complete(long now, SimulatedLink.Packet packet) {
            completed.add(packet.id());
        }
    }
}
