package org.bandwidthcontrol;

import java.io.BufferedWriter;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;

final class Events implements AutoCloseable {
    private final BufferedWriter writer;
    private final long session;

    Events(Path path, long session) throws IOException {
        writer = Files.newBufferedWriter(path);
        this.session = session;
        try {
            writer.write("elapsed_ns,event,session,sequence,frame,index,report,target_bps,application_bytes,ip_bytes,"
                         + "reason,report_age_ns,report_sample_ns,corrected_rtt_ns,window_ip_bytes,finalized_received,finalized_missing,transit_delta_us,echo_send_ns,echo_hold_us\n");
        } catch (IOException failure) {
            try {
                writer.close();
            } catch (IOException suppressed) {
                failure.addSuppressed(suppressed);
            }
            throw failure;
        }
    }

    void write(long now, String event, Long sequence, Long frame, Integer index, Long report, Integer target,
               Integer appBytes, Integer ipBytes, String reason, Long age, Long sample, Long rtt) throws IOException {
        write(now, event, sequence, frame, index, report, target, appBytes, ipBytes, reason, age, sample, rtt, null);
    }

    void writeReport(long now, String event, Datagrams.Feedback report, Integer target, Integer bytes,
                     String reason, Long age, Long rtt) throws IOException {
        Datagrams.Echo echo = report.echo();
        write(now, event, echo == null ? null : echo.sequence(), null, null, report.id(), target, bytes,
              bytes == null ? null : bytes + Datagrams.UDP_IP_BYTES, reason, age, report.sampleNs(), rtt, report);
    }

    private void write(long now, String event, Long sequence, Long frame, Integer index, Long report, Integer target,
                       Integer appBytes, Integer ipBytes, String reason, Long age, Long sample, Long rtt,
                       Datagrams.Feedback snapshot) throws IOException {
        String fields = ",,,,,,";
        if (snapshot != null) {
            Datagrams.Echo echo = snapshot.echo();
            fields = "," + snapshot.ipBytes() + "," + snapshot.received() + "," + snapshot.missing()
                     + "," + field(snapshot.timingUs()) + "," + field(echo == null ? null : echo.sendNs())
                     + "," + field(echo == null ? null : echo.holdUs());
        }
        writer.write(now + "," + event + "," + session + "," + field(sequence) + "," + field(frame) + ","
                     + field(index) + "," + field(report) + "," + field(target) + "," + field(appBytes) + ","
                     + field(ipBytes) + "," + reason + "," + field(age) + "," + field(sample) + "," + field(rtt)
                     + fields + "\n");
    }

    void note(long now, String event, String reason) throws IOException {
        write(now, event, null, null, null, null, null, null, null, reason, null, null, null);
    }

    private static String field(Object value) {
        return value == null ? "" : value.toString();
    }

    @Override
    public void close() throws IOException {
        writer.close();
    }
}
