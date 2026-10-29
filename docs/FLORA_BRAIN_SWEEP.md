# FlyWire subset input sweep — 2026-10-29

Reproduce with `.venv/bin/python python/sweep_stimulus.py`. Each row is the mean of seeds 1, 2, 3, using 200 ms of simulated time and the prepared 384-neuron subset. Subset SHA-256: `1932918c794f3228fcfb2d50521d9a15382ad277f91d46eccde3d1f09b235e67`. The subset contains 20,407 original nonzero connections after pinning MN9 (`720575940660219265`) as a readout. This changes the earlier unpinned 20,516-edge subset; it does not reproduce the whole fly brain.

| Input Hz | Active neurons | Downstream Hz (mean) | Downstream range Hz | MN9 Hz (mean) |
| ---: | ---: | ---: | ---: | ---: |
| 0 | 0.0 | 0.000 | 0.000–0.000 | 0.000 |
| 20 | 36.7 | 0.556 | 0.496–0.606 | 0.000 |
| 40 | 65.7 | 2.057 | 1.818–2.300 | 0.000 |
| 60 | 102.7 | 4.835 | 4.242–5.868 | 5.000 |
| 80 | 131.7 | 8.076 | 7.617–8.636 | 31.667 |
| 100 | 138.0 | 10.950 | 10.220–11.350 | 40.000 |
| 120 | 143.7 | 14.201 | 13.402–14.945 | 70.000 |
| 140 | 147.3 | 15.758 | 14.601–16.350 | 63.333 |
| 160 | 154.7 | 17.521 | 17.259–17.700 | 75.000 |
| 180 | 160.0 | 19.444 | 19.242–19.766 | 80.000 |
| 200 | 163.7 | 20.826 | 20.276–21.446 | 78.333 |
| 220 | 164.0 | 22.305 | 22.190–22.466 | 95.000 |
| 240 | 166.0 | 23.567 | 23.292–23.912 | 93.333 |
| 250 | 167.7 | 24.233 | 24.008–24.642 | 95.000 |

## Display policy

The application uses **wait** below 1.0 downstream Hz, **reach** from 1.0 to below 7.0 Hz, and **offer** at 7.0 Hz or above. In this sweep, the 20 Hz trials remained below 1.0, the 40–60 Hz trials fell between 1.0 and 7.0, and the 80 Hz and higher trials exceeded 7.0. These thresholds are an app-defined display mapping for operator discussion. They are not a biological claim, robot command, or safety decision. Only the existing operator controls can advance a flower placement.

MN9 is a named readout pinned into the isolated subset. Its rate should not be interpreted as a verified complete motor pathway because connections beyond the subset are omitted. The 200 ms window means each neuron rate changes in 5 Hz increments per spike.
