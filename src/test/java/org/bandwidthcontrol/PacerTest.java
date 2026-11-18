package org.bandwidthcontrol;

import static org.junit.jupiter.api.Assertions.assertEquals;

import org.junit.jupiter.api.Test;

class PacerTest {
    @Test
    void anchorsEachDeadlineToActualSendTime() {
        var pacer = new Pacer(1000, 800_000);
        assertEquals(0, pacer.nextNs());
        pacer.sent(0);
        assertEquals(10_000_000, pacer.nextNs());
        pacer.sent(50_000_000);
        assertEquals(60_000_000, pacer.nextNs());
        pacer.retry(62_000_000);
        assertEquals(63_000_000, pacer.nextNs());
        pacer.sent(63_000_000);
        assertEquals(73_000_000, pacer.nextNs());
    }
}
