package org.bandwidthcontrol;

import static org.junit.jupiter.api.Assertions.*;

import java.nio.ByteBuffer;
import java.util.HexFormat;
import org.junit.jupiter.api.Test;

class DatagramsTest {
    @Test
    void mediaGoldenVector() {
        byte[] bytes = new byte[540];
        byte[] header = HexFormat.of().parseHex(
                "41425743010100000000000000000001"
                + "0000000000000002000000000000000300000004010001f4");
        System.arraycopy(header, 0, bytes, 0, header.length);
        var packet = new Datagrams.Media(2, 3, 4, 1);
        assertEquals(packet, Datagrams.readMedia(ByteBuffer.wrap(bytes), 1));
        assertArrayEquals(bytes, Datagrams.media(ByteBuffer.allocate(540), 1, packet).array());
    }

    @Test
    void feedbackGoldenVector() {
        byte[] bytes = HexFormat.of().parseHex(
                "41425743010200000000000000000001"
                + "00000000000000010000000005f5e1000000047000020001ffffc56800030000"
                + "000000000000000200000000000000030000c35000000000");
        var packet = new Datagrams.Feedback(1, 100_000_000, 1136, 2, 1, -15_000,
                                            new Datagrams.Echo(2, 3, 50_000));
        assertEquals(packet, Datagrams.readFeedback(ByteBuffer.wrap(bytes), 1));
        assertArrayEquals(bytes, Datagrams.feedback(ByteBuffer.allocate(72), 1, packet).array());
    }

    @Test
    void rejectsShortLongAndWrongSessionDatagrams() {
        ByteBuffer data = ByteBuffer.allocate(541);
        Datagrams.media(data, 1, new Datagrams.Media(2, 3, 4, 1));
        assertThrows(Datagrams.InvalidPacket.class, () -> Datagrams.readMedia(data.duplicate(), 9));
        data.limit(539);
        assertThrows(Datagrams.InvalidPacket.class, () -> Datagrams.readMedia(data.duplicate(), 1));
        data.limit(541);
        assertThrows(Datagrams.InvalidPacket.class, () -> Datagrams.readMedia(data, 1));
    }

    @Test
    void rejectsAbsentEchoWithNonzeroFields() {
        ByteBuffer data = ByteBuffer.allocate(72);
        Datagrams.feedback(data, 1, new Datagrams.Feedback(0, 100_000_000, 0, 0, 0, null, null));
        data.putLong(56, 10);
        assertThrows(Datagrams.InvalidPacket.class, () -> Datagrams.readFeedback(data, 1));
    }
}
