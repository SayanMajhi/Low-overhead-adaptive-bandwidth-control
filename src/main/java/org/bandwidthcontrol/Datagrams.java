package org.bandwidthcontrol;

import java.nio.ByteBuffer;
import java.nio.ByteOrder;

final class Datagrams {
    static final int PAYLOAD_BYTES = 500;
    static final int MEDIA_BYTES = 540;
    static final int FEEDBACK_BYTES = 72;
    static final int UDP_IP_BYTES = 28;
    static final int MEDIA_IP_BYTES = MEDIA_BYTES + UDP_IP_BYTES;
    static final int FEEDBACK_IP_BYTES = FEEDBACK_BYTES + UDP_IP_BYTES;
    static final int MAX_WINDOW_PACKETS = 512;
    static final int HISTORY_SIZE = 4096;
    private static final int MAGIC = 0x41425743;
    private static final int TIMING_FLAG = 1;
    private static final int ECHO_FLAG = 2;
    private static final byte[] PAYLOAD = new byte[PAYLOAD_BYTES];

    record Media(long sequence, long sendNs, long frame, int index) {}
    record Echo(long sequence, long sendNs, long holdUs) {}
    record Feedback(long id, long sampleNs, int ipBytes, int received, int missing,
                    Integer timingUs, Echo echo) {}

    static ByteBuffer media(ByteBuffer out, long session, Media packet) {
        header(out, session, 1);
        out.putLong(packet.sequence()).putLong(packet.sendNs()).putInt((int) packet.frame());
        out.put((byte) packet.index()).put((byte) 0).putShort((short) PAYLOAD_BYTES).put(PAYLOAD);
        return out.flip();
    }

    static ByteBuffer feedback(ByteBuffer out, long session, Feedback packet) {
        header(out, session, 2);
        int flags = (packet.timingUs() == null ? 0 : TIMING_FLAG) | (packet.echo() == null ? 0 : ECHO_FLAG);
        out.putLong(packet.id()).putLong(packet.sampleNs()).putInt(packet.ipBytes());
        out.putShort((short) packet.received()).putShort((short) packet.missing());
        out.putInt(packet.timingUs() == null ? 0 : packet.timingUs()).putShort((short) flags).putShort((short) 0);
        Echo echo = packet.echo();
        if (echo == null) {
            out.putLong(0).putLong(0).putInt(0);
        } else {
            out.putLong(echo.sequence()).putLong(echo.sendNs()).putInt((int) echo.holdUs());
        }
        return out.putInt(0).flip();
    }

    static Media readMedia(ByteBuffer in, long session) {
        validateHeader(in, session, 1, MEDIA_BYTES);
        long sequence = nonnegative(in.getLong());
        if (sequence == Long.MAX_VALUE) {
            throw new InvalidPacket("sequence_overflow");
        }
        long sendNs = nonnegative(in.getLong());
        long frame = Integer.toUnsignedLong(in.getInt());
        int index = Byte.toUnsignedInt(in.get());
        if (index > 9 || in.get() != 0 || Short.toUnsignedInt(in.getShort()) != PAYLOAD_BYTES) {
            throw new InvalidPacket("media_fields");
        }
        return new Media(sequence, sendNs, frame, index);
    }

    static Feedback readFeedback(ByteBuffer in, long session) {
        validateHeader(in, session, 2, FEEDBACK_BYTES);
        long id = nonnegative(in.getLong());
        long sampleNs = nonnegative(in.getLong());
        long ipBytes = Integer.toUnsignedLong(in.getInt());
        int received = Short.toUnsignedInt(in.getShort());
        int missing = Short.toUnsignedInt(in.getShort());
        int timing = in.getInt();
        int flags = Short.toUnsignedInt(in.getShort());
        if ((flags & ~(TIMING_FLAG | ECHO_FLAG)) != 0 || in.getShort() != 0) {
            throw new InvalidPacket("feedback_flags");
        }
        long sequence = nonnegative(in.getLong());
        long sendNs = nonnegative(in.getLong());
        long holdUs = Integer.toUnsignedLong(in.getInt());
        boolean timingPresent = (flags & TIMING_FLAG) != 0;
        boolean echoPresent = (flags & ECHO_FLAG) != 0;
        if (in.getInt() != 0 || ipBytes > MAX_WINDOW_PACKETS * MEDIA_IP_BYTES
            || ipBytes % MEDIA_IP_BYTES != 0 || received + missing > HISTORY_SIZE
            || timing < -250_000 || timing > 250_000
            || (!timingPresent && timing != 0) || (!echoPresent && (sequence != 0 || sendNs != 0 || holdUs != 0))) {
            throw new InvalidPacket("feedback_fields");
        }
        return new Feedback(id, sampleNs, (int) ipBytes, received, missing, timingPresent ? timing : null,
                            echoPresent ? new Echo(sequence, sendNs, holdUs) : null);
    }

    private static void header(ByteBuffer out, long session, int type) {
        out.clear().order(ByteOrder.BIG_ENDIAN);
        out.putInt(MAGIC).put((byte) 1).put((byte) type).putShort((short) 0).putLong(session);
    }

    private static void validateHeader(ByteBuffer in, long session, int type, int bytes) {
        if (in.remaining() != bytes) {
            throw new InvalidPacket("length");
        }
        in.order(ByteOrder.BIG_ENDIAN);
        if (in.getInt() != MAGIC || in.get() != 1 || in.get() != type || in.getShort() != 0) {
            throw new InvalidPacket("header");
        }
        if (in.getLong() != session) {
            throw new InvalidPacket("session");
        }
    }

    private static long nonnegative(long value) {
        if (value < 0) {
            throw new InvalidPacket("u63_range");
        }
        return value;
    }

    static final class InvalidPacket extends IllegalArgumentException {
        InvalidPacket(String reason) {
            super(reason);
        }
    }
}
