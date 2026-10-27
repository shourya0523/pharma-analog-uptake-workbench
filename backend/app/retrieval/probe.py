"""Command line probe: ask Gemini for one product-period revenue and print it.

    python -m app.retrieval.probe "Calderon" "Q2 2023" --issuer "Acme Pharma"

Prints the model, the parsed answer, every citation and the searches the model
ran. `--save` writes the result as JSON so a run can be compared later.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from app.retrieval.gemini_search import QuotaExceeded, ask_revenue


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("product")
    parser.add_argument(
        "period",
        help='"Q2 2023" or "2021" are calendar dates; "FY2021" is the issuer\'s own fiscal year',
    )
    parser.add_argument("--issuer")
    parser.add_argument("--model")
    parser.add_argument("--save", type=Path)
    args = parser.parse_args(argv)

    try:
        answer = ask_revenue(args.product, args.period, args.issuer, model=args.model)
    except QuotaExceeded as exc:
        print(f"quota exceeded, not retried: {exc}", file=sys.stderr)
        print(
            "Search grounding needs a paid tier; check billing and usage at "
            "https://ai.dev/rate-limit",
            file=sys.stderr,
        )
        return 2

    print(f"model: {answer.model}")
    print(f"interaction: {answer.interaction_id}")
    print("searches:")
    for query in answer.search_queries:
        print(f"  {query}")
    print("answer:")
    if answer.parsed is not None:
        print(json.dumps(answer.parsed, indent=2))
    else:
        print("  (no JSON object in the reply; raw text follows)")
        print(answer.text)
    print("citations:")
    for citation in answer.citations:
        print(f"  {citation.url}  [{citation.title}]")
        if citation.cited_text:
            print(f"    cites: {citation.cited_text!r}")
    if answer.usage:
        print(f"usage: {json.dumps(answer.usage)}")

    if args.save:
        args.save.write_text(json.dumps(asdict(answer), indent=2) + "\n")
        print(f"saved: {args.save}")
    return 0 if answer.parsed is not None else 1


if __name__ == "__main__":
    sys.exit(main())
