# Case 02 · Draft agent prompts

For team review. The PDF describes reserve staff manually turning station readings into daily Excel tables, averages and charts (p. 1). Page 3 gives the calculation rules, optional tasks and submission requirements. Each prompt below is an independent assignment for the agreed scope.

## 1. Data audit and calculation rules

> Read `data/raw/case-2-weather.pdf` and the README inside `data/raw/meteo_hack_dataset.zip`. Deliver a reproducible EDA report and a tested calculation specification for the Central Forest Reserve. Reconcile station coverage with `coverage_by_month.csv`. Use a full calendar, preserve source timestamps, exclude conflict rows entirely and mask only the named parameter for each field-level flag. Report station coverage separately from usable coverage for a parameter.
>
> Count distinct timestamps for AME-60 (at least 72 of 96 per day) and distinct clock hours for Sokol M1 (at least 18 of 24). Compute daily means from all eligible readings, including multiple readings in one hour. A month passes with at most 10 incomplete days and a longest incomplete run of at most four. Ten-day groups end on day 10, day 20 and the last calendar day. Verify these boundaries with hand-calculated fixtures. Report the contributing year count for every selected-period mean. Keep stations separate across 2021/2022.
>
> Check the published Kr1 2024 pressure example independently. Give numerator, denominator and query for each result. Explain any difference from the archive README before handing the calculations to engineering.

## 2. Product and UX

> Design the workflow described on p. 1 of Case 02 for a reserve employee preparing a weather report in Excel. Start with one question the employee can answer using AME-60. Let them select the period and parameter, read the table, chart and statistics, and inspect why a day was omitted. Show units, valid days out of calendar days and the number of contributing years beside «среднее за выбранный период». Give a concrete empty state for the missing AME-60 wind direction in 1998. Keep scientific validity rules fixed while allowing choices of period, parameter, aggregation and comparison period.
>
> Deliver an annotated flow, Russian interface copy, prioritized backlog and an unassisted usability task with a checkable answer. Test whether participants can obtain the report and correctly explain a missing value. Record observations, completion and time for actual sessions. Treat faster-than-Excel claims as hypotheses until you measure both workflows. Keep the five optional extensions on p. 3 ranked separately by value, effort and data support.

## 3. Product engineering and release

> Build a runnable AME-60 reporting app from the approved flow and Case 02 rules. Support arbitrary date ranges and parameter selection, tables, charts, descriptive statistics, trends within a comparable series and deviations from a selected-period mean. Explain eligibility, units and years used. Keep instrument series separate. Preserve missing data and render a clear explanation for an unavailable parameter. Choose the stack against measured archive size, query latency and the actual deployment target.
>
> The PDF requires a working solution and a code repository with launch instructions. Page 3 lists optional Sokol reports, the 2025 glade–field comparison (monthly temperature differences and mean daily cycle), Sokol pressure, an external short-range forecast and same-format uploads. Implement agreed extensions after the core workflow passes. For any upload, test it with a new same-format file. Deliver a reproducible build, unit and integration tests, a browser smoke test, and a README naming the runnable release branch or tag and its startup command. If deployed, identify the deployed commit and verify the application URL from a fresh browser.

## 4. Independent QA and defense

> Validate the Case 02 release from a clean checkout. With independently calculated fixtures, test 71/72 AME-60 observations, 17/18 distinct Sokol hours, repeated readings within an hour, 10/11 missing days, runs of 4/5 missing days, leap February and a field flag alongside a usable value. Check that a conflict removes the entire row, a missing direction series yields no wind rose, and no trend bridges 2021/2022. For Kr1 in 2024, independently verify the archive's published figures: 363/366 station-valid days, 1,362 pressure values flagged, 334 pressure-valid days and 11 pressure-valid months, with April excluded for pressure. Use these as data regression checks even if Sokol screens are outside release scope.
>
> Run the brief's primary task in the release UI, on a narrow screen and by keyboard. Confirm table and chart match a source calculation, selection changes update both, gaps remain visible and labels explain exclusions. Deliver a release verdict, actual test results and a timed demo. Show the Excel task, generate the same report, explain one exclusion, and give the jury a reproducible number. Demonstrate optional features only when present in the verified release. Capture a fallback from that build and record its branch or tag and commit.
