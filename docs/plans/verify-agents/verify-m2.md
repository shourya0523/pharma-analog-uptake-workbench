---
name: verify-m2
description: Verifies the parsing claims - the sentence splitter, footnote scope binding, the document dating guard, and the discarded tagged spans.
model: opus
tools: Read, Grep, Glob, Bash
---

Follow `.claude/agents/_verification-protocol.md`. Change no file.

Module M2: `parsing/`. Items 5b, 5c, 6a, 7a, 7c.

- **7a** `periods.py:330` takes the latest year outright, dating a Perrigo
  FY2022 10-K to December 2040 and ANI's FY2025 to 2027; 15 of 235 datable
  documents pick a year named once over one named up to 180 times; the correct
  guard is claimed to exist at `periods.py:281`. Re-run over the cached corpus
  and state which documents you scanned.
- **6a** `sentences.py:25` splits on newlines, so an HTML table row becomes
  three sentences and `value_and_product_in_different_sentences` fires: 326
  rows, 212 with no other objection. Reproduce the split on a real cached
  document, and re-derive both counts from a run database.
- **5b** `labels.py:363 _note_scope` takes the first `_NOTE_PERIOD_RE` hit;
  `_NOTE_SPAN_RE` does not know "quarters ended"; 567 of 680 corpus notes
  return "parsed nothing" as "applies to the whole row"; "quarters ended"
  appears in 145 corpus files. Verify against the real ANI note.
- **5c** `labels.py:414-420` `_INCLUDES_RE` fires on "does not include"; the
  register says `_no_sales_of` at `:378` got every real ANI note right.
- **7c** `xbrl.py:139` returns None for any span but 3 or 12 months, claimed to
  discard 190 six- and nine-month tagged facts. Re-derive the 190 and say over
  what corpus.
