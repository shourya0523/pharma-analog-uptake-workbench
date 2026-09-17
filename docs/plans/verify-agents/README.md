# The verification agents, as a record

Eleven one-shot agent definitions, one per module, written for the pass
`docs/plans/2026-09-17-007-verify-then-fix.md` describes. Each was handed the
items of `006` its module owned, with the line references and measurements the
register carried at the time.

That pass is complete (`bba9ba6`), which is what makes them history rather than
tools. They pin line numbers that have since moved - `verify-m3.md:16` sends a
reader to `client.py:828` for a call to `re_ytd_language`, and
`backend/app/llm/client.py:824` is now `def apply_judge_hard_vetoes(` - and
they name register items that are fixed, so invoking one today points an agent
at code that is not there and at work that is done.

They are kept because the reports they produced are folded into `006` and this
is what was asked for them. What is still worth invoking lives in
`.claude/agents/`: `_verification-protocol.md`, which is the method rather than
one pass of it, and the reviewer definitions, which are scoped by module and
say nothing about a particular register.
