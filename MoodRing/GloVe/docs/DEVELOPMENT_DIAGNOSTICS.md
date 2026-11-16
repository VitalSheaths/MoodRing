# Development diagnostics and provenance

`research_data.development_diagnostics` binds ordered development predictions to
rows before computing QLIKE, volatility errors and floor flags. It reports fixed
coverage bins per ticker/channel: absent, one–three, four–seven and eight scored
days. It retains extreme losses and validates exact title hashes and availability
against forecast cutoffs. It does not infer that low coverage caused an error or
that a high-loss headline is incorrectly classified.

Review company role, topic and generic tone separately. A company may be a warning
source, product maker, recipient or merely one item in a digest. Competitor tone
must never be reversed into target-company sentiment. Human labels do not overwrite
model probabilities. Diagnostic convenience samples, including AI-assisted reviews,
do not establish population sentiment accuracy.

Scheduled-event evidence must establish what was knowable before each forecast.
A later article describing a large move cannot supply an earlier earnings-calendar
feature. These packet experiments do not add event-calendar inputs. Keep event
clustering, source traces and reviewer notes private; public tests use fictional
records. Exact review overlays preserve source records and observation times while
excluding specified company/source/input mappings from new candidate votes.

Prior model findings remain bound to their own artifacts and configuration-selection
rules. A variance-floor failure remains visible even after a separately approved
positive-output experiment. Coverage/timing hypotheses are development evidence,
not permission to inspect or select on the final test.
