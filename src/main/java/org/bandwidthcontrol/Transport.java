package org.bandwidthcontrol;

import java.io.IOException;
import java.nio.ByteBuffer;

@FunctionalInterface
interface Transport {
    int write(ByteBuffer datagram) throws IOException;
}
