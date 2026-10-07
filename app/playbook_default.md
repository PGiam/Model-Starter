# Model-building playbook

This playbook is read by Claude before every model. Team feedback is turned into
proposed edits here and applied only after an admin approves them.

## Workflow
1. Ask for the ticker if one wasn't given. Look up the company and its fiscal year end.
2. List the past five years of 10-Ks, 10-Qs, earnings releases (8-K Item 2.02, Exhibit 99.1) and, where the company posts them, earnings call transcripts. Save the documents to the session's downloads so the analyst can open them, and share the list of links.
3. Read the most recent 10-K business and MD&A sections and the latest two earnings releases. Note the reported segments and the operating KPIs management discloses.
4. Propose a driver-based model structure: which segment revenue drivers (volume x price or similar), margin drivers, cost drivers and working-capital drivers you will use, and which company KPIs or third-party series back each one.
5. Propose a list of competitors and ask the analyst to add or remove names. Wait for that answer before building.
6. Gather the history, save the model spec, build the workbook, and fix every validation error before presenting it.
7. Present the download link, the key assumptions that move the forecast most, any data gaps, and ask for edits.

## Model conventions
- Use fiscal quarters. Every column shows the fiscal quarter label and its period-end date.
- History goes back five years of quarters (or as far as the company has public filings). The forecast runs five fiscal years forward.
- All three statements sit on the Quarterly Model tab; the Annual Model tab rolls them up by fiscal year; Key Metrics shows RoE, RoA, RoIC, inventory turns, DSO, DIO, DPO, cash conversion cycle, net debt / EBITDA and related ratios; Competitors shows peer metrics and relative revenue and profit share.
- Every source link goes in a clickable cell on the Data Sources tab, never in a cell comment.
- The model is formula-driven: forecast drivers are inputs, everything else is a formula.

## Data sources
- Company filings and releases come first.
- Outside driver data should come from government or industry databases that publish at least quarterly (for example BTS, BLS, BEA, Census, the Federal Reserve/FRED, industry associations and established industry indices). Record the publisher and frequency for each series.
- Record the URL for every number you take from a document.

## Communication
- Keep chat replies short and plain. Lead with what you need from the analyst.
- Say clearly when a value was estimated or could not be found.
