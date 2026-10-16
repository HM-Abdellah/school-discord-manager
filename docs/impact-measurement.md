# Measuring School Discord Manager's Real-World Impact

Use this guide during the school pilot to collect evidence for the project's portfolio. Do not invent estimates or present hypothetical numbers as real results.

## Before the school starts using the bot

1. Select workflows the school already performs and that the bot actually supports, such as publishing a teacher-absence notice, registering a student via QR, or completing a class transfer.
2. For each workflow, observe at least 5 comparable cases using the existing/manual process. Record the elapsed time in seconds and whether the task was completed correctly.
3. Record the date range, task definition, number of cases, and any factors that could affect the result. Do not record student names, teacher names, Discord IDs, or message contents in this measurement sheet.
4. If the school has already started using the bot, do not reconstruct a baseline from memory. Label any retrospective estimate clearly as an estimate and keep it separate from measured results.

## After rollout

Repeat the same measurement for the same task definition, with at least 5 comparable cases performed using the bot. Include time spent preparing the command or correcting failures, not only the time needed to click the command.

Use the median time to reduce the effect of unusually fast or slow cases.

- Time saved per case = baseline median − bot median.
- Time reduction (%) = (baseline median − bot median) / baseline median × 100.
- If the bot median is higher, report the increase rather than describing it as a saving.
- If the task definition or sample sizes differ materially, explain that limitation instead of making a direct comparison.

## Measurement table

Copy one row per workflow and fill in the measured values only.

| Workflow | Baseline sample count | Baseline median (seconds) | Bot sample count | Bot median (seconds) | Time saved per case (seconds) | Time reduction (%) | Measurement dates / notes |
|---|---:|---:|---:|---:|---:|---:|---|
| Example placeholder — replace with a real workflow | 5 | — | 5 | — | — | — | Do not publish until measured |

## Monthly usage report

A configured administrator can run `/usagereport days:30` in the school server. For an annual period, `/usagereport days:365` exports the most recent 365 calendar days. The bot keeps a local CSV copy under `data/reports/` and sends an aggregate copy to the administrator as an ephemeral Discord attachment.

The report contains daily counts of slash-command callbacks and selected audited action names. A completed command callback is not automatically proof that the underlying business operation succeeded. The report is not a full transaction ledger, and it does not automatically measure time saved. Pair its activity counts with the baseline measurements above and the school's qualitative feedback.

Back up the entire `data/` directory, including `school.db`, `archives/`, and `reports/`, as part of the school's normal backup process. Keep a separate backup outside the bot's working folder and test that the backup can be restored. A report stored only on the same PC can be lost if that PC or disk fails.

## Evidence for a project portfolio

For each reporting period, retain only material approved for sharing by the school:

- The aggregate CSV report and a brief explanation of its date range and limitations.
- The baseline/post-rollout measurement table, including sample counts and measurement dates.
- The project's GitHub release or commit reference and test results for that version.
- A school confirmation letter, if the school is willing to confirm that the bot was used and describe the observed benefit.
- A short demo recorded with dummy data or appropriately anonymized screens.

Never publish the school's bot token, database, raw security audit log, student or teacher identities, private messages, or unredacted screenshots. Obtain the school's approval before using its name, logo, screenshots, or testimonial in a public portfolio.
