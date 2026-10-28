# Run summary: <run id = YYYY-MM-DD-slug>

Written by the author of the run. Facts and diagnostics only; interpretation belongs in a
separate document that reads this file. A field that could not be produced says
`not produced: <reason>` — never an estimate.

## Provenance
| Field | Value |
|---|---|
| Repo, commit | `<repo>` @ `<git sha>` (dirty: yes/no) |
| Command | `<exact command line>` |
| Profile | engineer / data_cleaning / model_fitting / bug_hunting / figures |
| Machine, environment | `<host>`, `<uv lock or env name>` |
| Random seeds | `<list>` |
| Inputs | `<path>` (`<rows or bytes>`, `<hash or mtime>`) … |
| Wall time | `<value> s` |

## Model and configuration
- Model / pipeline stage: `<name>`; configuration file: `<path>`.
- Free parameters (count): `<n>`; fixed parameters: `<list or pointer>`.
- Data selection applied (for cleaning runs, one row per cut):

| # | Cut expression | Rows before | Rows after | Removed (%) | Reference |
|---|---|---|---|---|---|

## Best-fit parameters
| Parameter | Value | Uncertainty | Unit | Prior | At bound? |
|---|---|---|---|---|---|

Samples / covariance file: `<path>`.

## Diagnostics (physical first)
| Diagnostic | Value | Threshold or reference | Pass? |
|---|---|---|---|
| Reduced chi-square (dof) | | | |
| Residual vs `<variable>`: max binned offset / scatter | | | |
| Pull distribution: mean, width, tail fraction beyond 3σ | | | |
| Posterior predictive check on `<statistic>` | | | |
| Convergence: R-hat max, ESS min, acceptance | | | |
| Dimensional / physical assertions | | | |
| Secondary (loss, RMSE, accuracy) | | | |

Residual arrays: `<path>`.

## Outputs
| Path | What it is |
|---|---|

Figures (diagnostic only): `<path>` — `<what is plotted>` …

## Checks and status
- Gates passed: `<list>`; gates failed: `<list>`.
- Change log for this run (any re-cut, re-fit, or parameter change and why): …
- Blockers / open items: …
- Needs my decision: …

## Not interpreted
No physical conclusion is drawn here. Interpretation: `reports/<date>-<topic>.md`
(later analysis), if and when written.
