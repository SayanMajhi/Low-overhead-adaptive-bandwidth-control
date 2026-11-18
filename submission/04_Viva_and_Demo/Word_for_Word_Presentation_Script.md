# Word-for-word English presentation script

For the [eight-slide PowerPoint with Wireshark captures](../01_Presentation/ppt.pptx). Allow about five minutes. Read the quoted paragraphs aloud. The italic lines are cues for you.

## Slide 1 — Introduction and objective

> Good morning, everyone. Our project is called Low-Overhead Adaptive Bandwidth Control. We built a Java sender and receiver that exchange synthetic video packets over UDP.
>
> When bandwidth drops, packets can build up in a queue. Some may arrive after their deadline. The receiver sends feedback to help the sender adjust its rate, but those reports also use bandwidth. We wanted to see whether we could send fewer reports while keeping useful data arriving on time and responding quickly to bandwidth changes.

## Slide 2 — Literature review

> Google Congestion Control showed us how changes in packet delay can help detect congestion. RFC eighty-eight eighty-eight describes what information an RTCP feedback report can carry. RFC ninety-three ninety-two discusses how report timing affects overhead and responsiveness.
>
> We used these ideas to design our own experiment. We kept the sender’s control rules and the receiver’s measurements the same, then changed when the receiver sent its reports. Our project uses its own UDP message format.

## Slide 3 — Our method

*Point to Periodic, then Adaptive.*

> With periodic feedback, the receiver sends a report every one hundred milliseconds. With adaptive feedback, the receiver can send a report early when it sees sustained signs of congestion. If no earlier report goes out, the receiver checks in after five hundred milliseconds.
>
> We ran four hundred and fifty tuning simulations to choose and check settings. Then we evaluated the fixed settings in two thousand three hundred and ten held-out simulations. Each pair faced the same planned network conditions. We also ran twenty-four live UDP trials to verify packet exchange on an isolated network.

## Slide 4 — Design and architecture

*Follow the arrows from sender to receiver, then point to the return arrow.*

> The sender creates synthetic video frames and spaces out the packets. The packets travel over the network to the receiver. The receiver measures packet loss and changes in delay.
>
> For example, if two packets were sent twenty milliseconds apart but arrived thirty-five milliseconds apart, the second packet spent fifteen milliseconds longer in transit. That can be a sign of a growing queue. The receiver reports what it sees, and the sender uses that information to adjust the rate for future packets.

## Slide 5 — Results

*Point to the bar chart first. Then show the periodic and adaptive Wireshark screenshots.*

> On our main simulation, adaptive used sixty-eight point seven six percent fewer feedback bytes. The periodic runs sent nine hundred thousand feedback bytes, and the adaptive runs sent two hundred eighty-one thousand two hundred. We subtracted the adaptive total from the periodic total and divided by the periodic total.
>
> We also checked if enough useful data still arrived on time. Our lower confidence bound was ninety-four point four nine percent, just below the required ninety-five percent. Adaptive also took four hundred and thirty-three milliseconds longer to settle after bandwidth fell and six hundred and forty-seven milliseconds longer after it rose.
>
> The Wireshark screenshots show a separate demo. Both programs ran on the same computer. Over roughly eight seconds of active sending, periodic sent seventy-nine feedback reports, and adaptive sent fourteen. Their median gaps were ninety-four and five hundred and forty-five milliseconds. We get each gap by subtracting the previous report’s timestamp from the next one.

## Slide 6 — Challenges

> One challenge is that a queue can stay full after delay stops increasing. A signal based on changes in delay can then miss how long packets are still waiting. That may help explain why adaptive completed only about thirty-eight percent of base frames on time in the stable low-bandwidth test, compared with about ninety-nine percent for periodic.
>
> Another challenge is that fewer reports can leave the sender waiting for fresh information after a bandwidth change. Both extra settling times exceeded our two-hundred-millisecond limit. We measured delivery of synthetic frames, so real playback quality would need a separate test.

## Slide 7 — Conclusion and future work

> We built and tested a working UDP system. Adaptive reporting saved feedback bytes, but it missed our goodput confidence target and took too long to settle after both bandwidth changes. Five of our eight checks passed. We required all eight to pass.
>
> We also tried a two-hundred-millisecond heartbeat on three tuning seeds. It settled faster and sent more feedback, with mixed results for on-time delivery. Next, we would test that version on fresh workloads, look for a better way to detect queues that stay full, and measure actual video playback.

## Slide 8 — References

> Our main references were the Google Congestion Control paper and the two RFCs on congestion feedback and report timing. The project report, saved simulation results, and Wireshark captures provide the evidence for the results we presented.
>
> Thank you. I’m happy to take questions.
