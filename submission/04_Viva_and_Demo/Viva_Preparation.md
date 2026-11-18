# Viva preparation

Practice explaining the project in your own words. The selected reference paper is historical GCC.

## 1. Why choose GCC?

**Short answer:** It provides close prior art for delay-aware media control.

**Technical detail:** The repository names several references, so we selected the 2016 paper as the reference for this presentation.

**Evidence:** Carlucci et al., DOI 10.1145/2910017.2910605.

## 2. What did the paper propose?

**Short answer:** A delay-aware congestion controller.

**Technical detail:** It filters inter-group delay variations and uses an adaptive threshold with sender and receiver rate decisions.

**Evidence:** Carlucci et al., sections 3–4.

## 3. What did the paper measure?

**Short answer:** Utilization, delay, loss and fairness in controlled tests.

**Technical detail:** One three-flow 3 Mb/s case reported 86% utilization and Jain fairness 0.93.

**Evidence:** Carlucci et al., Table 2.

## 4. Did we reproduce GCC?

**Short answer:** We built our own simplified controller.

**Technical detail:** Our timing signal and event feedback draw on prior work; the custom implementation has different state and wire format.

**Evidence:** docs/PROJECT.md#research-question.

## 5. What is our contribution?

**Short answer:** A controlled cadence comparison and Java testbed.

**Technical detail:** Periodic and adaptive arms share the sender law and observations so timing is the intended intervention.

**Evidence:** docs/PROJECT.md#system-architecture.

## 6. What is novel?

**Short answer:** The measured trade-off for this implementation and workloads.

**Technical detail:** Event-triggered reporting already has prior art; do not claim a new congestion-control principle.

**Evidence:** GCC paper; RFC 8298; study report.

## 7. Why does congestion increase delay?

**Short answer:** Packets wait when arrivals exceed service capacity.

**Technical detail:** The bottleneck FIFO accumulates packets until the sender slows or the queue drops traffic.

**Evidence:** docs/PROJECT.md#experiment-design-and-analysis.

## 8. Why UDP?

**Short answer:** It gives the application control over timing and loss handling.

**Technical detail:** The prototype adds its own sequencing, pacing and feedback.

**Evidence:** src/main/java/org/bandwidthcontrol/.

## 9. What is goodput?

**Short answer:** Useful payload delivered on time per second.

**Technical detail:** The study counts unique payload bytes delivered by frame deadlines and divides by measurement duration.

**Evidence:** docs/PROJECT.md#experiment-design-and-analysis.

## 10. How does RTT differ from one-way delay?

**Short answer:** RTT is a round trip measured on the sender clock.

**Technical detail:** The live measurement subtracts receiver hold; independent JVM origins make direct one-way subtraction invalid.

**Evidence:** docs/PROJECT.md#evidence-and-verification.

## 11. How is delay change measured?

**Short answer:** Compare arrival spacing with send spacing.

**Technical detail:** Constant clock offset cancels in the interval difference; drift and jitter can still bias it.

**Evidence:** docs/PROJECT.md#system-architecture.

## 12. What happens with packet loss and jitter?

**Short answer:** Loss removes payload; jitter varies arrival time.

**Technical detail:** Reordering is allowed for 75 ms before a missing sequence is finalized.

**Evidence:** ReceiverMeasurements.java.

## 13. What is pacing?

**Short answer:** Spread packets over time.

**Technical detail:** A 568-byte IP packet at 800 kbit/s takes 5.68 ms of target-rate budget.

**Evidence:** Pacer.java; transport evaluation.

## 14. How does the controller react?

**Short answer:** It reduces rate for high loss or increasing delay.

**Technical detail:** A high hybrid signal multiplies the target by 0.85, subject to cooldown and bounds.

**Evidence:** RateController.java; Congestion.java.

## 15. What are the event thresholds?

**Short answer:** Two high samples enter the reporting latch.

**Technical detail:** High means qualified loss of at least 5% or delay change at least 12 ms.

**Evidence:** FeedbackSchedule.java; frozen profile.

## 16. What is feedback overhead?

**Short answer:** The bytes spent on receiver reports.

**Technical detail:** Each report is 100 IPv4 bytes, and emitted reports count even if lost.

**Evidence:** docs/PROJECT.md#experiment-design-and-analysis.

## 17. Why can feedback be too sparse?

**Short answer:** The sender learns about changes later.

**Technical detail:** Heartbeat spacing and lost reports can delay both congestion response and recovery.

**Evidence:** RFC 9392; study step results.

## 18. How are stale reports handled?

**Short answer:** Old or invalid evidence cannot drive a fresh increase.

**Technical detail:** Echo age, report ordering and local timeout determine whether the controller accepts an observation.

**Evidence:** Sender.java; RateController.java.

## 19. How do we prevent oscillation?

**Short answer:** We bound rate changes and use persistence and cooldown.

**Technical detail:** These mechanisms reduce sensitivity to individual noisy observations, but stability was not guaranteed.

**Evidence:** FeedbackSchedule.java; RateController.java.

## 20. Why use paired seeds?

**Short answer:** Both policies encounter the same exogenous path history.

**Technical detail:** Packet timings and resulting queue drops can differ legitimately as rates diverge.

**Evidence:** experiments/protocol/freeze.json.

## 21. Why 30 runs?

**Short answer:** The study fixed 30 seed pairs before evaluation.

**Technical detail:** Paired bootstrap intervals quantify variability across those seeds under each fixed scenario.

**Evidence:** docs/PROJECT.md#research-question.

## 22. How was the slower baseline chosen?

**Short answer:** By matching feedback bytes on tuning data.

**Technical detail:** 350 ms minimized the difference to adaptive across ten tuning seeds; delivery metrics did not select it.

**Evidence:** docs/PROJECT.md#evidence-and-verification.

## 23. What was the main result?

**Short answer:** Feedback bytes fell 68.76% in the fluctuating workload.

**Technical detail:** Goodput retention lower confidence bound was 94.49%, below the 95% requirement.

**Evidence:** experiments/results/simulation/analysis/held-out/analysis.json.

## 24. Why did step results fail?

**Short answer:** Adaptive took longer to settle after capacity changes.

**Technical detail:** Its mean extra settle time was 433 ms down and 647 ms up; the allowed margin was 200 ms.

**Evidence:** docs/PROJECT.md#evidence-and-verification.

## 25. Why can delay-based detection miss a standing queue?

**Short answer:** A stable queue can have little further delay growth.

**Technical detail:** The gradient may become neutral while absolute queueing delay stays large; traces support this as an interpretation.

**Evidence:** docs/PROJECT.md#results-and-limitations.

## 26. What did the 200 ms candidate prove?

**Short answer:** It produced promising but mixed tuning results.

**Technical detail:** Three seeds showed faster settling, mixed goodput and about twice the feedback; no new held-out confidence analysis exists.

**Evidence:** docs/PROJECT.md#results-and-limitations.

## 27. Were real packets tested?

**Short answer:** Yes, across 24 isolated UDP trials.

**Technical detail:** Two policies, four conditions and three repetitions used verified path captures and queue counters.

**Evidence:** docs/PROJECT.md#evidence-and-verification.

## 28. Does this prove better video quality?

**Short answer:** The tests measure synthetic delivery proxies.

**Technical detail:** There was no encoded video playback or viewer study.

**Evidence:** docs/PROJECT.md#results-and-limitations.

## 29. What about fairness?

**Short answer:** Competing traffic was tested as fixed background load.

**Technical detail:** A fairness claim against a responsive TCP flow requires another experiment.

**Evidence:** docs/PROJECT.md#experiment-design-and-analysis.

## 30. How do you reproduce the work?

**Short answer:** Use the frozen inputs, JAR, Java version and runner.

**Technical detail:** The manifest pins hashes and seeds; the README documents replay and audit.

**Evidence:** experiments/protocol/freeze.json; README.md.

## 31. What would you do next?

**Short answer:** Test queue-aware signals and a wider range of paths.

**Technical detail:** Define a new protocol and held-out workload before claiming improved delivery.

**Evidence:** docs/PROJECT.md#results-and-limitations.

## 32. Was AI used?

**Short answer:** Yes, AI assisted the project work.

**Technical detail:** Explain the parts you personally understand and can demonstrate, and identify AI assistance honestly.

**Evidence:** docs/PROJECT.md#results-and-limitations.

