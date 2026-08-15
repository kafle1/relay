# Cover letter, IEEE Transactions on Intelligent Transportation Systems

Paste into the ScholarNet / ScholarOne cover letter box. Written to be pasted as-is.

---

Dear Editor-in-Chief,

I am submitting "A Low-Cost Single-Camera Adaptive Signal Controller for Heterogeneous Urban
Traffic: Self-Calibrating Pressure Control with Bounded Fairness and Emergency Preemption" for
consideration as a regular paper.

The paper addresses signal control in cities where the detector plant that SCOOT, SCATS, OPAC and
RHODES assume is not affordable, and where traffic is motorcycle-dominated and strongly asymmetric
between approaches. The controller takes its only input from one existing fixed junction camera.

Three contributions distinguish it from the applied camera-and-timer literature. First, the
controller self-calibrates: it estimates each approach's arrival and discharge rate from the count
sequence alone, and uses those estimates to floor each stage's green at its share of Webster's cycle
evaluated on the measured degree of saturation, and to price a stage change against the discharge
its clearance interval forfeits. No saturation-flow table, site survey or per-site constant is
required. Second, fairness is enforced by explicit bounds rather than by the score, including an
anti-starvation rule that serves the worst-treated queue rather than rotating among equally starved
ones. Third, the evaluation is against a Webster plan computed from the true mean demand, which is
considerably harder to beat than the equal-split baseline this literature usually adopts, and it is
reported per cell across 72 cells rather than as an average.

I would draw the reviewers' attention to three things I have tried to report honestly rather than
favourably. The corridor coordination term produces no measurable benefit, changing mean delay by at
most 0.9 per cent, so the corridor gain comes from local adaptivity and I say so. One cell of the
72-cell grid is lost to the fixed plan, and the paper explains why rather than dropping it. The
perception stack undercounts severely on motorcycle-dense footage, and rather than testing robustness
against convenient zero-mean noise alone, I repeat the full grid under biased count models that match
the failure the detector actually exhibits.

The work is a simulation and real-footage feasibility study. There is no field installation and no
before-and-after study on a live signal, and the paper states which claims a field trial would still
have to establish. I believe it is at the level a city could use to decide whether a pilot is worth
funding, which is the decision this class of deployment actually faces.

All experiment scripts and raw per-run result files accompany the manuscript. Every run is seeded and
the reproduction is exact: re-running the robustness experiment from a clean clone returns all 72
cells identical to the values reported. A checker distributed with the code reads the numerical
claims out of the manuscript and recomputes each from the raw results.

The manuscript is original, is not under consideration elsewhere, and has no prior publication. I am
the sole author and declare no conflicts of interest.

Yours sincerely,

Niraj Kafle
Kathmandu, Nepal
contact.me.kafle@gmail.com
https://github.com/kafle1/relay

---

# Declarations to enter on the submission form

**Originality.** The manuscript is original, unpublished, and not under consideration at any other
journal or conference.

**Authorship.** Sole author. No other person meets the authorship criteria.

**Conflicts of interest.** None.

**Funding.** None. The work was conducted independently and unfunded.

**Data availability.** All experiment scripts, configuration files and raw per-run result files are
available at https://github.com/kafle1/relay under AGPL-3.0.

**Ethics.** No human or animal subjects. The real footage used for the perception evaluation is
publicly available fixed-camera traffic video and contains no identifiable personal data at the
resolution used.

**Use of AI tools.** Fill this one in yourself, and read the journal's current policy first, since
the wording changes. If you use assistance for language editing or for checking numbers against the
data, the accurate and safe declaration is along these lines:

> The author used an AI assistant for language editing and for verifying the consistency of reported
> numerical values against the raw result files. The author designed the study, implemented the
> controller and simulation, ran all experiments, interpreted the results, and takes full
> responsibility for the content of the manuscript.

That statement is accurate, it is the kind editors see routinely, and it costs nothing. Undisclosed
assistance is grounds for retraction under COPE guidance, and a retraction would follow you into
every scholarship and visa application you make. Declare it.
