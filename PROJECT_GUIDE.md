# Low-overhead adaptive bandwidth control — final project guide

**Study path:** Problem → Meaning → Research → Experiments → Design reasons → Working solution → Results and lessons → Improvement → Next steps.

Read each section at three levels: a 5-year-old analogy for intuition, a BTech explanation for your viva, and engineer detail to follow the code and verify the evidence. Sections 9–12 are your file map, networking glossary, demo, and judge script.

## 1. Problem and meaning

**5-year-old:** You send pieces of a picture to a friend. Your friend sometimes says, “Pieces are getting stuck. Slow down!” You want fewer messages from your friend, but you still want the warning in time.

**BTech:** A link can carry only a certain number of bits per second. If the sender exceeds that capacity, packets wait in a queue or get dropped. For live video, even a delivered packet can be useless if it arrives after its frame deadline. The receiver sends feedback so the sender can adjust its rate. “Low overhead” means fewer feedback bytes on the return path. “Adaptive” means the receiver changes when it reports based on what it observes.

**Engineer:** The intervention is feedback cadence. The primary arms use the same sender controller, packet format, pacing rules, and receiver measurements. One reports every 100 ms. The other reports on sustained congestion and checks in with a 500 ms heartbeat. The hypothesis requires less feedback **and** acceptable delivery and response. A reduction in feedback traffic alone cannot pass it.

**Open these files:** The [README](README.md) defines the project scope. The [study reference](report.md) explains the research question, prior work, and experiment design.

## 2. Research and design rationale

**5-year-old:** We tried two ways for the friend to talk. In one, they check in on a timer. In the other, they speak sooner if they see trouble.

**BTech:** Google Congestion Control (GCC) was the reference for delay-based congestion detection. We built a smaller UDP system to isolate one question about reporting frequency. Our measured baseline was our own 100 ms periodic policy. We did not run a benchmark against GCC or a commercial product.

**Engineer:** Changing the controller and feedback policy together would confound the comparison. The same controller can still produce different rates because it receives different observations at different times. Seven simulator arms help distinguish the effects.

| Choice | Reason |
| --- | --- |
| Java 21 and Maven | Build a runnable, tested implementation and reuse the endpoint logic in socket and virtual-clock modes. |
| UDP | Let the application choose its own pacing, feedback format, and handling of late, lost, or reordered packets. |
| Synthetic video | Control frame timing, packet sizes and deadlines without introducing a video encoder or playback pipeline. |
| Fixed-size packets | Make pacing and byte accounting reproducible. |
| Simulator | Replay identical exogenous conditions, inspect queues, and measure deadlines on a common clock. |
| Isolated real UDP | Check that packet exchange and feedback work across actual sockets and a controlled path. |
| Fixed vs. loss-only AIMD | Provide simpler controls for interpreting the shared delay/loss controller. |
| Heartbeat-only | Show what periodic check-ins do without early congestion events. |
| Matched-budget periodic | Ask whether adaptive timing adds value compared with a slower timer at approximately comparable feedback cost. |
| No-delay periodic | Show how removing the delay signal affects behavior while keeping periodic reporting. |

**Follow the evidence:** Read the [research report](report.md) for the distinction between prior research and our own results. Open the [tuning scenarios](config/scenarios/tuning) and [held-out scenarios](config/scenarios/held-out) to see that they are separate sets.

## 3. Experiment pipeline and analysis

**5-year-old:** Give both ways of talking the same traffic problems. Repeat the race many times and count warnings, picture pieces that make it in time, and how fast each reacts.

**BTech:** We used tuning data to choose settings and then kept the settings fixed for the held-out experiment. A seed lets the simulator reproduce its network impairment draws. Pairing means comparing the two policies under the same scenario and seed. Their queues can still differ because their packet timings differ.

**Engineer:**

1. **Tune on 450 runs.** Six workloads and separate tuning seeds. On the variable workload, choose the periodic interval whose total feedback bytes are closest to adaptive. This picked **350 ms**, based on feedback bytes alone. Actual costs can differ in held-out cases.
2. **Freeze inputs before evaluation.** Record profile and workload hashes, build and runtime identities, seeds, comparison arms and study rules. Keep the archived JAR for exact replay.
3. **Run 2,310 held-out simulations.** 11 scenarios × 30 seeds × seven arms. The scenarios cover stable high and low capacity, downward and upward steps, fluctuating capacity, jitter, random loss, burst loss, cross traffic, feedback loss and feedback delay.
4. **Analyze 30 paired runs per comparison.** Resample whole seed pairs 100,000 times to get confidence bounds. Measuring a whole pair preserves the shared conditions. Feedback savings and goodput retention are ratios of arm means. The completion loss and p95 delay increase use paired differences.
5. **Run 24 real UDP trials.** Four conditions × two policies × three repetitions. The conditions are clean, capped, forward capacity step, and reverse-path delay. Use isolated Linux networking, captures and queue counters to verify the intended path effects. Live delivered throughput and corrected RTT use different measurements from simulated deadline goodput.

| Metric | Exact interpretation |
| --- | --- |
| Feedback bytes | Emitted IPv4 report bytes. A report dropped on the reverse path still incurred overhead when sent. |
| On-time goodput | Unique media payload bytes received by each frame deadline, converted to bits per second. |
| On-time base completion | Fraction of generated frames whose two required packets arrive by the deadline. |
| Packet p95 delay | A delay at or below which roughly 95% of valid packet arrivals fall. Read this alongside loss and completion. |
| Settling | Target rate enters its post-step band and stays there for 500 ms, within a 5 s observation window. Settled counts and truncated settling times are separate criteria. |

**Follow one experiment from input to result:** [simulation-campaign.py](scripts/simulation-campaign.py) expands the [frozen protocol](experiments/protocol/freeze.json) into runs. The [held-out run list](experiments/results/simulation/held-out/run-list.csv) identifies each archived run. [evaluate.py](analysis/evaluate.py) turns those outputs into run tables and paired estimates. [plot_results.py](analysis/plot_results.py) creates the figures. [live-audit.py](experiments/verification/live-audit.py) checks the separate real-UDP evidence.

## 4. Why the solution uses these control rules

**5-year-old:** Wait to see if the problem keeps happening before warning. Say something occasionally even when things look fine. When the warning comes, slow down first and speed up carefully later.

**BTech:** Requiring repeated samples reduces sensitivity to a single noisy measurement. Minimum spacing limits report bursts. The heartbeat keeps information coming during quiet periods. A long heartbeat saves more bytes but can delay the sender’s next decision.

**Engineer:** The frozen profile starts at 1 Mb/s and bounds the rate to 0.5–2.272 Mb/s. A high signal is loss ≥5% with at least ten finalized outcomes, or usable delay change ≥12 ms. Low thresholds include loss ≤2% with sufficient outcomes and delay change ≤4 ms for the delay-enabled policy. The gap between thresholds helps avoid rapid switching. The hybrid congestion factor is 0.85 with 200 ms spacing between congestion reductions. Healthy evidence permits growth at 300,000 bit/s per second of credited time. The profile uses a 750 ms freshness limit and an 850 ms stale threshold. Stale evidence can trigger a rate reduction. The study freezes these settings for comparison. The specific numbers are experimental settings, with no claim of global optimality. Read the exact rules in [the engineering reference](report.md) and [the frozen profile](config/profiles/frozen-study.json).

## 5. Solution pipeline and file-by-file walkthrough

**5-year-old:** Make picture pieces → send them one at a time → watch for pieces that arrive late → tell the sender how things are going → change how fast the next pieces go.

```text
Frame source → Sender + pacer → Forward path → Receiver + measurements
                    ↑                              ↓
              Rate controller ← Reverse path ← Feedback scheduler
```

| Stage | BTech explanation and reason | Engineer detail |
| --- | --- | --- |
| Generate | Create 50 frames/s so both policies start with the same workload. Base packets represent required pieces. Enhancement packets represent optional detail. | One frame every 20 ms, two base and eight enhancement packets offered, 500 payload bytes each, deadline at creation +150 ms. Local source discards are recorded separately from network losses. |
| Pace | Spread packets out instead of sending a whole burst. | A media packet costs 568 IPv4 bytes, so at 1 Mb/s it uses 4.544 ms of rate budget. Schedule from actual successful send time to avoid catch-up bursts. |
| Carry | Bottleneck queues and impairments determine when packets arrive. | Simulator uses separate forward and reverse links, byte-limited FIFO queues, capacity schedules and time-indexed impairment traces. The controller has no access to link truth. |
| Measure | Detect loss and signs of growing queue delay. Give an out-of-order packet time to arrive. | Sample every 50 ms over a 250 ms window. Allow 75 ms before finalizing a missing sequence outcome. Compare arrival spacing with send spacing. |
| Report | Send feedback on a timer or earlier when there is sustained trouble. | The periodic arm uses 100 ms. Adaptive uses two successive high samples to enter the event state, 100 ms minimum event spacing, two low samples to leave the state, and a 500 ms heartbeat. |
| Adjust | Reduce rate when congestion is high and increase gradually when conditions look healthy. | Sender ticks every 100 ms and consumes at most one new admissible observation per tick. The hybrid controller applies thresholds, freshness checks, cooldown, and rate bounds. |

**Timing example:** Packets leave 20 ms apart and arrive 35 ms apart. Their relative transit time increased by 15 ms. Comparing intervals cancels a constant offset between the two clocks. Jitter and clock drift can still affect the signal. Two independent JVM timestamps cannot be subtracted directly to obtain one-way delay.

**Wire format:** A 16-byte header identifies protocol magic (`ABWC`), version, packet type, and session. Media carries sequence, sender timestamp, frame ID, packet index, and payload. Feedback carries report ID, observation time, delivered bytes and loss counts, delay-change information, and a sender timestamp echo when available. The application payloads are **540 bytes for media** and **72 for feedback**. Add 8 bytes of UDP and 20 bytes of IPv4 overhead to get **568** and **100 IPv4 bytes** respectively. These accounting sizes assume ordinary IPv4 headers without fragmentation. The echo lets the sender estimate round-trip time on its own clock after removing the receiver’s reported hold time.

**Trace the code in order:** [Main](src/main/java/org/bandwidthcontrol/Main.java) accepts the command. [SimulationCommand](src/main/java/org/bandwidthcontrol/SimulationCommand.java) loads the inputs and selects the arm for [Simulation](src/main/java/org/bandwidthcontrol/Simulation.java). In live mode, [Transport](src/main/java/org/bandwidthcontrol/Transport.java) drives the UDP endpoints. Then follow [Sender](src/main/java/org/bandwidthcontrol/Sender.java) → [Pacer](src/main/java/org/bandwidthcontrol/Pacer.java) → [Datagrams](src/main/java/org/bandwidthcontrol/Datagrams.java) → [SimulatedLink](src/main/java/org/bandwidthcontrol/SimulatedLink.java) or UDP → [Receiver](src/main/java/org/bandwidthcontrol/Receiver.java) → [ReceiverMeasurements](src/main/java/org/bandwidthcontrol/ReceiverMeasurements.java) → [FeedbackSchedule](src/main/java/org/bandwidthcontrol/FeedbackSchedule.java) → [RateController](src/main/java/org/bandwidthcontrol/RateController.java). [Events](src/main/java/org/bandwidthcontrol/Events.java) records what happened, and [Metrics](src/main/java/org/bandwidthcontrol/Metrics.java) computes the simulator summary.

## 6. Results and what we learned

**5-year-old:** The friend sent many fewer warnings. But when the road changed speed, the sender took longer to catch up. In one very slow-road test, many picture pieces arrived too late.

**BTech:** The implementation worked in simulation and over real UDP. The adaptive policy saved return-path traffic against our periodic baseline on the main workload. It missed the predefined goodput and response requirements. Our evidence supports that specific savings claim and identifies the cost.

**Engineer:**

| Gate | Observed result and decision |
| --- | --- |
| Feedback savings | 68.76%. Lower bound 68.56% exceeds the 25% requirement. Pass. |
| Base completion loss | −0.16 percentage points. Upper bound +0.29 pp is within the 2 pp margin. Pass. |
| Goodput retention | 95.10% estimate, but lower bound 94.49% is below 95%. Miss. |
| Packet p95 delay increase | +0.92 ms. Upper bound +2.43 ms is within the 20 ms margin. Pass. |
| Downstep settling increase | +433 ms. Upper bound +550 ms exceeds the 200 ms margin. Miss. |
| Upstep settling increase | +647 ms. Upper bound +720 ms exceeds the 200 ms margin. Miss. |
| Downstep settled count | 30/30 for each policy. Pass. |
| Upstep settled count | 30/30 for each policy. Pass. |

Five gates cleared. All eight were required, so the joint hypothesis was unsupported. On stable-low, adaptive base completion was **38.29%** versus **99.16%** periodic. A plausible explanation supported by the trace is that a queue can stay full while its delay gradient becomes small. On the fluctuating workload, adaptive had **90.09%** base completion versus **80.07%** for 350 ms periodic, with about **9% more feedback bytes** in that comparison. All 24 live trials passed the transport audit. The capped adaptive trials exhibited higher RTT despite similar delivered throughput.

**Judge wording:** “Compared with our 100 ms periodic baseline on the main workload, we reduced feedback bytes by 68.76%. The full performance hypothesis was unsupported. We would need a direct benchmark to compare with current commercial systems.”

**See the evidence:** The [paired results figure](experiments/summary/figures/paired-effects.png) shows the measured comparisons. The [stable-low trace](experiments/summary/figures/stable-low-trace.png) shows what happened in that workload. The [verification reference](report.md) describes the evidence checks.

## 7. Improvement tested

**5-year-old:** We made the friend check in sooner. The sender could react faster, but the friend had to talk more.

**BTech:** We tried shortening the heartbeat from 500 ms to 200 ms. A shorter wait gives the sender fresher information. The trade-off is more return-path traffic.

**Engineer:** The exploratory [200 ms profile](config/profiles/fast-feedback-candidate.json) showed faster settling across the tested step cases on three tuning seeds. Goodput improved in the variable and downstep tuning cases and declined in the upstep case. Feedback roughly doubled. These are tuning observations. A fresh held-out evaluation is needed to assess all eight requirements.

## 8. Next steps

**5-year-old:** Check the new way on roads we haven't tried, and see whether it notices a traffic jam that's already stopped moving.

**BTech:** Retest the shorter heartbeat on fresh workloads, improve detection of persistent queues, and try real encoded video.

**Engineer:** Predefine new held-out workloads for the 200 ms candidate. Compare feedback cost, deadlines, and step timing under the same acceptance rules. Test a signal sensitive to sustained queue delay and check its behavior under jitter, reordering and reverse-path delay. Then benchmark with encoded media and a contemporary reference system using comparable traffic and network conditions.

## 9. Repository and Java class map

**5-year-old:** Think of a school bag with separate pockets for instructions, building pieces, practice sheets, and finished work.

**BTech:** The source builds the program, configuration describes the tests, and experiment folders hold the raw and analyzed results.

**Engineer:** Trace the entry point, then the shared endpoints, then the virtual-clock or socket driver. Use the configuration and output manifests to establish which version and conditions produced a result.

| Directory or file | What it contains |
| --- | --- |
| [README.md](README.md) | Setup, CLI commands, replay and headline results. |
| [pom.xml](pom.xml) | Maven dependencies, Java 21 configuration, tests and executable JAR packaging. |
| [src/main/java](src/main/java) | Implementation. Use the class map below. |
| [src/test/java](src/test/java) | JUnit checks for pacing, codec, feedback, receiver measurements, controller, links and simulator CLI. |
| [config/profiles](config/profiles) | Controller and reporting settings. Use `frozen-study.json` for the main study, `baselines.json` and `controller-candidate.json` for earlier checks, and `fast-feedback-candidate.json` for the follow-up. |
| [config/scenarios](config/scenarios) | Network workloads split into tuning and held-out sets. |
| [config/fixtures](config/fixtures) | Small correctness input for smoke checks. |
| [scripts](scripts) | `simulation-campaign.py` executes the run matrix. `udp-smoke.py` checks local UDP. `live-udp/` runs isolated trials. `component-timing/` contains exploratory JVM timing. |
| [analysis](analysis) | `evaluate.py` computes results, `BootstrapIndices.java` generates paired resampling indices, `plot_results.py` draws figures, and `evidence_paths.py` resolves relocated evidence. |
| [experiments/protocol](experiments/protocol) | Frozen study manifest. |
| `experiments/results/` (full local evidence) | Simulation archives, run lists, figures, live UDP records, loopback evidence and faster-heartbeat runs. |
| [experiments/verification](experiments/verification) | Simulator and controller checks, scenario validation, UDP loopback harness and simulation/live audit tools. |
| [experiments/reproducibility](experiments/reproducibility) | Original campaign runner retained for exact archived-plan replay. |
| [Project reference](report.md) | System design, experiments, findings and verification. |
| [submission](submission/START_HERE.md) | Final presentation, report, standalone project, viva materials and verification records. |
| [Study provenance](experiments/protocol) | Frozen protocol and original detailed technical records. |

### Java class map

All classes are under [org/bandwidthcontrol](src/main/java/org/bandwidthcontrol).

| Class | Responsibility |
| --- | --- |
| `Main` | CLI entry point for `send`, `receive`, and `simulate`. |
| `Transport` | Actual UDP I/O and endpoint timing. |
| `Sender`, `Pacer` | Frame creation, packet eligibility, send spacing, and applying the current target. |
| `Receiver`, `ReceiverMeasurements` | Receive packets, track sequence outcomes and build feedback observations. |
| `Datagrams` | Wire encoding, decoding and format validation. |
| `FeedbackSchedule`, `Congestion` | Decide when to report and classify delay/loss signals. |
| `RateController` | Rate updates, cooldown, growth credit and stale-feedback handling. |
| `SimulationCommand`, `SimulationConfig`, `SimulationArm` | Parse and validate run inputs and select the policy. |
| `Simulation`, `SimulatedLink`, `ImpairmentTrace` | Drive virtual time, serve queues and generate repeatable path conditions. |
| `Events`, `Metrics` | Write telemetry and derive simulator run summaries. |

**Engineer reading order:** Start in `Main`, follow `SimulationCommand` into `Simulation`, then trace `Sender → Receiver → FeedbackSchedule → RateController`. Open `Transport` to see how real UDP drives the same endpoint logic. Read `Metrics` and `analysis/evaluate.py` to see the distinction between a run’s measurements and conclusions across runs.

## 10. Computer Networks concepts and glossary

Read this table left to right. Start with the analogy, then learn the formal networking term and find its implementation.

| Concept | Like you're 5 | BTech explanation | Engineer view and where it appears |
| --- | --- | --- | --- |
| **Layering and encapsulation** | A picture piece goes inside an envelope with an address. | The application builds a message, UDP adds ports, and IPv4 adds network addresses. | A 540-byte media message becomes a 568-byte IPv4 packet after adding 8 bytes of UDP and 20 of IPv4. A 72-byte feedback message becomes 100 IPv4 bytes. See `Datagrams` and `Transport`. |
| **UDP and application protocol** | You can mail pieces without waiting for each one to be confirmed. | UDP sends datagrams without guaranteeing delivery, order, or retransmission. The application defines its own media and feedback messages. | `Transport` uses UDP sockets. `Datagrams` encodes frame IDs, sequence numbers, timestamps and report IDs. Feedback conveys observations about the stream. |
| **Bandwidth, bottlenecks and queueing** | When bags arrive faster than a narrow door lets them through, they line up. | Bandwidth is the link's carrying capacity. A bottleneck can build a queue when offered traffic exceeds its service rate. | `SimulatedLink` models capacity, FIFO service and a finite byte queue. Arrivals that do not fit are dropped. Scenario files describe changes in available capacity. |
| **Packet pacing** | Put one bag through at a time instead of dumping them all at the door. | Pacing spreads packets over time to control the offered rate and reduce bursts. | `Pacer` spaces departures based on the current rate and IPv4 packet size. The next eligible time is based on the actual send. |
| **Delay, jitter and reordering** | Some deliveries take longer, so pieces can arrive in a different order. | Delay is travel time. Jitter is variation in delay. Sequence numbers reveal gaps, and waiting briefly helps distinguish reordering from loss. | `ReceiverMeasurements` tracks sequences and allows 75 ms before finalizing a missing outcome. Its delay signal compares arrival spacing with send spacing. The simulator measures transit delay on a shared clock. |
| **Congestion control and AIMD** | Send more slowly when the path gets crowded. Speed back up a little at a time. | Congestion control adapts the offered rate to network conditions. Additive increase and multiplicative decrease (AIMD) is a standard pattern. | `RateController` implements the study's hybrid delay/loss policy. The experiment also includes a loss-only AIMD comparison. `Congestion` classifies receiver evidence. |
| **Feedback and the reverse path** | Your friend's warning can also get stuck on the way back. | The sender's view depends on how often the receiver reports and how quickly those reports travel back. | `FeedbackSchedule` implements periodic and event-triggered reporting. The simulator models separate forward and reverse paths. Live trials deliberately added reverse-path delay. |
| **RTT and clock scope** | Time how long it takes to hear back using your own stopwatch. | Round-trip time measures a there-and-back interval. Absolute one-way delay requires a shared or aligned time reference. | The live sender uses an echoed send timestamp and subtracts receiver hold time to estimate corrected RTT. Live endpoint elapsed clocks have separate origins. |
| **Throughput, goodput and deadlines** | Count the picture pieces you can still use when it's time to show the picture. | Throughput measures delivery rate under a stated byte definition. On-time goodput counts useful payload that arrives before its deadline. | `Metrics` separates IPv4 feedback cost, delivered payload, on-time payload and base-frame completion. A packet can count toward delivered throughput and still miss its frame deadline. |

**Viva distinction:** Flow control protects the receiving endpoint from being overwhelmed. Congestion control adapts the sending rate to conditions along the network path. This project studies congestion control and how the timing of receiver feedback affects it.

The main concepts form a loop. The sender **paces UDP packets** over a path with a **bottleneck queue**. The receiver measures **delay change and loss**, sends reports over the **reverse path**, and the sender uses those reports for **congestion control**. We judge the loop by **feedback overhead, on-time goodput, and response to capacity changes**.

See the [Java class map](#java-class-map) for the code and the [engineering reference](report.md) for packet formats and timing details.

**Units to know:** A bit is a single 0 or 1. A byte is 8 bits. A millisecond (ms) is one thousandth of a second. Mb/s measures millions of bits per second. A percentage point is the difference between two percentages, such as 95% minus 94% = 1 percentage point.

## 11. Run the demo and inspect the evidence

**5-year-old:** Give both senders the same road and watch how often each friend calls back.

**BTech:** Run both policies with the same scenario and seed, then compare their saved summaries.

**Engineer:** Build and test the jar, run each arm into a fresh directory, and verify the impairment trace hashes before comparing metrics.

From the repository root with Java 21 and Maven installed:

```sh
mvn -B clean verify
DEMO_DIR=$(mktemp -d)
for arm in periodic adaptive; do
  java -jar target/bandwidth-control.jar simulate \
    --scenario config/scenarios/tuning/tune-variable.json \
    --profile config/profiles/frozen-study.json \
    --arm "$arm" --seed 520101 --out "$DEMO_DIR/$arm"
done
printf 'Demo files: %s\n' "$DEMO_DIR"
```

1. Open both `manifest.json` files and point to the matching `exogenousTraceSha256`. This shows that the paths were driven by the same pre-generated conditions.
2. Open both `summary.json` files and compare `feedback_ip_bytes`, `on_time_payload_goodput_bps`, and `base_completion_ratio`. This is one tuning example.
3. Use `sender.csv` for rate decisions, `receiver.csv` for emitted reports and packet arrivals, `links.csv` for queue events, and `emission.csv` for binned sending activity. The run directory also preserves the profile and scenario.
4. Use the full [held-out analysis](experiments/summary/analysis.json) for numerical claims. [Run-level data](experiments/summary/runs.csv) and [paired effects](experiments/summary/effects.csv) sit beside it. Use the [live audit table](experiments/summary/live-audited.csv) for the real UDP results.
5. For a real socket demonstration, follow the [two-terminal UDP demo](README.md#real-udp-demo). That fixed-rate demo shows packet exchange and pacing. For the adaptive-policy comparison, use the paired simulator demo above.

## 12. Tell the whole story to a judge

**5-year-old:** We built a way to send picture pieces and learned that fewer warnings sometimes meant slower reactions.

**BTech:** Explain the problem, the two policies, the fair comparison, and the trade-off before quoting numbers.

**Engineer:** Name the baseline, workload, metric, and decision rule when presenting a measured claim.

> We built a Java UDP sender and receiver to study how often a video receiver should report network conditions. Feedback uses return-path bandwidth, but if it arrives too rarely, the sender may react late. We used synthetic video frames with known deadlines and built a simulator that reuses the endpoint logic. We changed only the feedback schedule between the main comparison arms. The periodic receiver reported every 100 milliseconds. The adaptive receiver could report early during sustained congestion and used a 500 millisecond heartbeat.
>
> We ran 450 tuning simulations, 2,310 held-out simulations, and 24 real UDP trials. The adaptive policy used 68.76 percent fewer feedback bytes on our main workload. It missed the goodput confidence requirement and took longer to settle after capacity changes. We then tried a shorter heartbeat. It settled faster in three tuning seeds, with mixed goodput and more feedback. Our contribution is the working testbed and the measured trade-off. The next step is a fresh evaluation of that version and a better signal for a queue that stays full.

| If they ask | What to say |
| --- | --- |
| Is it only research? | It is a Computer Networks implementation and experimental evaluation. Show the sender, receiver, simulator, and a paired run. |
| What did you contribute? | The custom Java testbed, its feedback policies and the controlled evaluation. Delay and event-triggered feedback have prior art. Describe the pieces you personally implemented or verified and disclose AI assistance accurately. |
| Why is a negative result still useful? | It identifies which requirements failed and where the savings become costly. The result answers the original research question for the tested settings. |
| Why not just report every 200 ms? | Faster reporting can improve response and increase overhead. Our 200 ms **heartbeat** still allows earlier event reports. The candidate has only tuning evidence. |
| How can I verify your claims? | Run the two policies with the same seed, inspect packet and report logs, and use the preserved held-out tables and verification tools for aggregate conclusions. |
