# Success Metrics & Counter-Metrics — pm-ai

Companion to `SPEC.md`, cited by the kernel's Success signal. Targets are **provisional first-release figures set on judgement rather than measurement** and should be re-baselined against real telemetry after the first month of operation (see the kernel's Assumptions).

## Primary success metrics

| Id | Metric | Target | Validates |
| --- | --- | --- | --- |
| **SM-1** | Executive bandwidth reclaimed — weekly meeting hours reduced through async inquiry proxies and pre-meeting relevance checks | ≥20% reduction | CAP-26 |
| **SM-2** | Voice response latency — end-to-end from a 20-second voice instruction to approved, dispatched multi-channel replies | <60 seconds | CAP-19, CAP-21 |
| **SM-3** | Socratic coaching utility — post-1:1 Coaching Efficiency score averaged across monthly retrospectives | ≥7 / 10 | CAP-12, CAP-14 |
| **SM-4** | Literature relevance rate — contextual recommendations rated actionable or relevant during 1:1s | ≥80% | CAP-17 |
| **SM-5** | Economic & power cost efficiency — total monthly operating cost (API spend + electrical power) against the $20/user monitored target, every frontier call attributed by task class | tracked, not enforced | NFR-13 |
| **SM-6** | Deep inquiry & meeting preparation accuracy — multi-source telemetry, pre-meeting status validations, and drift queries validated by the PM without manual re-queries | ≥90% | CAP-23, CAP-24, CAP-25, CAP-32, CAP-33, CAP-34 |
| **SM-7** | Spoken anchor & in-meeting command execution precision — anchors (including fuzzy-recovered) and direct verbal commands parsed and executed without manual correction | ≥95% | CAP-1, CAP-5, CAP-6, CAP-7, CAP-36 |
| **SM-8** | Implicit update approval accuracy — implicit updates staged in approval cards accepted by the PM without complete rejection | ≥80% | CAP-6, CAP-34 |
| **SM-9** | Closed-loop commitment verification precision — accuracy of automated status transitions to `[FULFILLED]` versus manual override | ≥90% | CAP-33, CAP-34 |

**SM-5 is a signal, not a pass/fail condition.** A breach is a prompt to investigate; it never triggers degradation.

## Counter-metrics — do not optimize

| Id | Do not optimize for | Instead | Counterbalances |
| --- | --- | --- | --- |
| **SM-C1** | Raw volume of generated drafts | Draft acceptance rate without extensive manual edits, ≥85% | SM-2 |
| **SM-C2** | Number of articles recommended per week | Cap at 3 situational citations per week, to avoid cognitive spam | SM-4 |
| **SM-C3** | Coaching session frequency | Respect PM-initiated cadence; never force daily prompts | SM-3 |

## The one metric whose improvement is suspicious

The self-tuning loop treats a rising coaching score as evidence of a problem when challenge falls with it — question ratio, blind spots surfaced, or experiments proposed declining while ratings improve means the adaptation is refused and logged as suppressed. SM-3 climbing is only good news alongside sustained challenge. The same guard applies on the retrieval axis via novelty.

Domain Distress is captured at every session and is **never** an input to tuning: it measures the PM's world, not pm-ai's performance.
