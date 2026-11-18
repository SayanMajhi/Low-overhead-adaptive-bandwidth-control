package org.bandwidthcontrol;

import static org.junit.jupiter.api.Assertions.*;

import org.junit.jupiter.api.Test;

class ReceiverMeasurementsTest {
    @Test
    void differentiatesReorderedPacketsFromMissingAndLateArrivals() {
        var receiver = new ReceiverMeasurements();
        assertEquals("in_order", receiver.receive(media(10), ms(0)).kind());
        assertEquals("in_order", receiver.receive(media(12), ms(10)).kind());
        assertEquals("reordered", receiver.receive(media(11), ms(15)).kind());
        assertEquals("duplicate", receiver.receive(media(12), ms(20)).kind());
        assertEquals("in_order", receiver.receive(media(14), ms(25)).kind());
        receiver.advance(ms(100), true);
        assertEquals("late_after_gap", receiver.receive(media(13), ms(101)).kind());
        var result = receiver.snapshot(0, ms(150), ms(150));
        assertEquals(5 * 568, result.ipBytes());
        assertEquals(4, result.received());
        assertEquals(1, result.missing());
    }

    @Test
    void acceptsGapClosingPacketExactlyAtTheReorderHorizon() {
        var receiver = new ReceiverMeasurements();
        receiver.receive(media(0), 0);
        receiver.receive(media(2), ms(10));
        assertEquals("reordered", receiver.receive(media(1), ms(85)).kind());
        receiver.advance(ms(85), true);
        assertEquals(0, receiver.snapshot(0, ms(100), ms(100)).missing());
    }

    @Test
    void echoDoesNotUseAnArrivalAfterTheNominalSnapshot() {
        var receiver = new ReceiverMeasurements();
        receiver.receive(media(0), 0);
        receiver.receive(media(1), ms(110));
        var report = receiver.snapshot(0, ms(100), ms(120));
        assertEquals(568, report.ipBytes());
        assertEquals(new Datagrams.Echo(0, 0, 120_000), report.echo());
    }

    @Test
    void relativeTrendCancelsAConstantClockOffset() {
        var receiver = new ReceiverMeasurements();
        for (int i = 0; i <= 4; i++) {
            receiver.receive(new Datagrams.Media(i, ms(i * 25L), i, 0), ms(8000 + i * 30L));
        }
        var report = receiver.snapshot(0, ms(8150), ms(8150));
        assertEquals(20_000, report.timingUs());
    }

    @Test
    void largeJumpResetsPendingGapsAndOldPacketsCannotGrowHistory() {
        var receiver = new ReceiverMeasurements();
        receiver.receive(media(10), 0);
        receiver.receive(media(12), ms(1));
        assertTrue(receiver.receive(media(9000), ms(2)).discontinuity());
        assertEquals("outside_history", receiver.receive(media(11), ms(3)).kind());
        assertEquals(0, receiver.snapshot(0, ms(150), ms(150)).missing());
    }

    private static Datagrams.Media media(int sequence) {
        return new Datagrams.Media(sequence, ms(sequence), sequence, 0);
    }

    private static long ms(long value) {
        return value * 1_000_000;
    }
}
