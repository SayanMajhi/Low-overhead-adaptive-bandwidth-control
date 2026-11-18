package org.bandwidthcontrol;

import java.util.ArrayDeque;
import java.util.HashMap;
import java.util.TreeMap;

final class ReceiverMeasurements {
    static final long NO_TIME = Long.MAX_VALUE;
    static final long SAMPLE_NS = 50_000_000;
    static final long WINDOW_NS = 250_000_000;
    static final long REORDER_NS = 75_000_000;
    private static final int HISTORY = Datagrams.HISTORY_SIZE;
    private static final long MAX_HOLD_US = 0xffff_ffffL;

    record Admission(String kind, boolean discontinuity) {}
    record Arrival(Datagrams.Media media, long time, boolean timingEligible) {}
    record Outcome(long time, boolean received) {}

    private final TreeMap<Long, Arrival> arrivalsBySequence = new TreeMap<>();
    private final HashMap<Long, Long> sequenceByIdentity = new HashMap<>();
    private final HashMap<Long, Long> gaps = new HashMap<>();
    private final ArrayDeque<Arrival> arrivals = new ArrayDeque<>();
    private final ArrayDeque<Outcome> outcomes = new ArrayDeque<>();
    private long anchor = NO_TIME;
    private long frontier = -1;
    private long highest = -1;
    private long lastTimingSendNs = -1;
    private long arrivalOverflow = -1;
    private long outcomeOverflow = -1;
    private final ArrayDeque<Arrival> echoCandidates = new ArrayDeque<>();
    private long lastFinalizedTime;

    Admission receive(Datagrams.Media data, long now) {
        if (anchor == NO_TIME) {
            anchor = now;
        }
        // An arrival at the exact expiry can still fill a hole.
        advance(now, false);

        long sequence = data.sequence();
        if (sequence < highest - HISTORY + 1) {
            return new Admission("outside_history", false);
        }
        if (arrivalsBySequence.containsKey(sequence)) {
            return new Admission("duplicate", false);
        }
        long identity = data.frame() * 10 + data.index();
        if (sequenceByIdentity.containsKey(identity)) {
            return new Admission("identity_conflict", false);
        }

        boolean discontinuity = highest >= 0 && sequence - frontier >= HISTORY;
        if (highest < 0 || discontinuity) {
            frontier = sequence;
            highest = sequence - 1;
            gaps.clear();
            if (discontinuity) {
                arrivals.clear();
                outcomes.clear();
                arrivalsBySequence.clear();
                sequenceByIdentity.clear();
                echoCandidates.clear();
                lastFinalizedTime = now;
                lastTimingSendNs = -1;
                arrivalOverflow = -1;
                outcomeOverflow = -1;
            }
        }

        boolean reordered = sequence < highest;
        boolean late = sequence < frontier;
        boolean timingEligible = !reordered && data.sendNs() >= lastTimingSendNs;
        Arrival arrival = new Arrival(data, now, timingEligible);
        arrivalsBySequence.put(sequence, arrival);
        sequenceByIdentity.put(identity, sequence);
        if (timingEligible) {
            lastTimingSendNs = data.sendNs();
        }
        if (sequence > highest) {
            for (long gap = highest + 1; gap < sequence; gap++) {
                gaps.put(gap, now + REORDER_NS);
            }
            highest = sequence;
            echoCandidates.addLast(arrival);
            if (echoCandidates.size() > HISTORY) {
                echoCandidates.removeFirst();
            }
        }

        while (arrivalsBySequence.firstKey() < highest - HISTORY + 1) {
            Datagrams.Media evicted = arrivalsBySequence.pollFirstEntry().getValue().media();
            sequenceByIdentity.remove(evicted.frame() * 10 + evicted.index());
        }
        gaps.remove(sequence);
        arrivals.addLast(arrival);
        if (arrivals.size() > 2 * Datagrams.MAX_WINDOW_PACKETS) {
            arrivalOverflow = arrivals.removeFirst().time();
        }
        advance(now, false);
        return new Admission(late ? "late_after_gap" : reordered ? "reordered" : "in_order", discontinuity);
    }

    void advance(long now, boolean inclusive) {
        while (frontier >= 0 && frontier <= highest) {
            Arrival delivery = arrivalsBySequence.get(frontier);
            if (delivery != null) {
                addOutcome(delivery.time(), true);
            } else {
                long expires = gaps.get(frontier);
                if (expires > now || (expires == now && !inclusive)) {
                    return;
                }
                gaps.remove(frontier);
                addOutcome(expires, false);
            }
            frontier++;
        }
    }

    private void addOutcome(long now, boolean received) {
        lastFinalizedTime = Math.max(lastFinalizedTime, now);
        outcomes.addLast(new Outcome(lastFinalizedTime, received));
        if (outcomes.size() > 2 * HISTORY) {
            outcomeOverflow = outcomes.removeFirst().time();
        }
    }

    long anchor() {
        return anchor;
    }

    long nextGapExpiry() {
        return gaps.getOrDefault(frontier, NO_TIME);
    }

    Datagrams.Feedback snapshot(long id, long sample, long now) {
        long cutoff = sample - WINDOW_NS;
        while (!arrivals.isEmpty() && arrivals.getFirst().time() <= cutoff) {
            arrivals.removeFirst();
        }
        while (!outcomes.isEmpty() && outcomes.getFirst().time() <= cutoff) {
            outcomes.removeFirst();
        }
        int received = 0;
        int missing = 0;
        for (Outcome outcome : outcomes) {
            if (outcome.time() <= sample && outcome.time() > cutoff) {
                if (outcome.received()) {
                    received++;
                } else {
                    missing++;
                }
            }
        }
        int available = 0;
        int eligible = 0;
        Arrival first = null;
        Arrival last = null;
        for (Arrival arrival : arrivals) {
            if (arrival.time() > sample) {
                break;
            }
            available++;
            if (arrival.timingEligible()) {
                if (first == null) {
                    first = arrival;
                }
                last = arrival;
                eligible++;
            }
        }
        Integer timing = null;
        if (eligible >= 5 && last.media().sendNs() - first.media().sendNs() >= 100_000_000) {
            long timingUs = ((last.time() - first.time()) - (last.media().sendNs() - first.media().sendNs())) / 1000;
            if (timingUs >= -250_000 && timingUs <= 250_000) {
                timing = (int) timingUs;
            }
        }

        Datagrams.Echo echo = null;
        Arrival newest = null;
        for (Arrival candidate : echoCandidates) {
            if (candidate.time() > sample) {
                break;
            }
            newest = candidate;
        }
        if (newest != null) {
            while (echoCandidates.getFirst() != newest) {
                echoCandidates.removeFirst();
            }
            long holdUs = (now - newest.time()) / 1000;
            if (holdUs <= MAX_HOLD_US) {
                echo = new Datagrams.Echo(newest.media().sequence(), newest.media().sendNs(), holdUs);
            }
        }
        if (available > Datagrams.MAX_WINDOW_PACKETS || received + missing > HISTORY
            || (arrivalOverflow >= 0 && arrivalOverflow > cutoff)
            || (outcomeOverflow >= 0 && outcomeOverflow > cutoff)) {
            return new Datagrams.Feedback(id, sample - anchor, 0, 0, 0, null, echo);
        }
        return new Datagrams.Feedback(id, sample - anchor, available * Datagrams.MEDIA_IP_BYTES,
                                      received, missing, timing, echo);
    }
}
