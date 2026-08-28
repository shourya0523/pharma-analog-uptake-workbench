import ast
import importlib.util
import re
from pathlib import Path

from app.quality.candidate_filters import filter_revenue_candidates
from app.quality.checks import quote_contains_value
from app.quality.comparative import parse_numbers

REPO_ROOT = Path(__file__).resolve().parents[2]
ACTELION_PDF = Path(__file__).resolve().parent / "fixtures" / "Actelion_Historical_Sales_Schedule.pdf"


def _load_script(name: str):
    path = REPO_ROOT / "scripts" / name
    spec = importlib.util.spec_from_file_location(name.removesuffix(".py"), path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_gold_research_script_does_not_import_the_pipeline():
    tree = ast.parse((REPO_ROOT / "scripts" / "research_gold_from_filings.py").read_text())
    imported = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.append(node.module or "")
    assert all("orchestrator" not in name for name in imported)
    assert all("pipeline" not in name for name in imported)


def test_pipeline_eval_refuses_to_write_seed_gold():
    module = _load_script("build_gold_web_search.py")
    gold = REPO_ROOT / "seed" / "gold"
    assert module.pipeline_eval_targets_gold(gold)
    assert module.pipeline_eval_targets_gold(gold / "nested")
    assert not module.pipeline_eval_targets_gold(Path("/tmp/pipeline-eval"))


def test_actelion_schedule_parser_reads_ww_line_with_production_helpers():
    module = _load_script("research_gold_from_filings.py")
    text = module.pdf_text(ACTELION_PDF.read_bytes())
    rows = module.parse_actelion_schedule(text, module.ACTELION_SALES_URL)
    by_drug = {drug: [row for row in rows if row["drug_name"] == drug] for drug, _ in module.ACTELION_PRODUCTS}
    assert set(by_drug) == {drug for drug, _ in module.ACTELION_PRODUCTS}
    for drug, _generic in module.ACTELION_PRODUCTS:
        drug_rows = by_drug[drug]
        assert [row["period"] for row in drug_rows] == module.ACTELION_WW_PERIODS
        match = re.search(
            rf"{drug}\s*\nUS[^\n]*\nIntl[^\n]*\nWW\s+([^\n]+)",
            text,
            re.I,
        )
        assert match
        nums = parse_numbers(match.group(1))
        for row, expected in zip(drug_rows, nums[1:6], strict=True):
            assert row["value_reported"] == expected
            assert quote_contains_value(row["source_quote"], row["value_reported"])
            kept, dropped = filter_revenue_candidates(
                [
                    {
                        "period": row["period"],
                        "value_reported": row["value_reported"],
                        "revenue_scope": row["revenue_scope"],
                        "source_quote": row["source_quote"],
                        "period_type": "quarterly",
                        "currency": "USD",
                    }
                ],
                product=drug,
                generic=row["generic_name"],
            )
            assert kept and not dropped
    q4_2016 = module.ACTELION_WW_PERIODS[1]
    tracleer = next(row for row in rows if row["drug_name"] == "Tracleer" and row["period"] == q4_2016)
    assert tracleer["source_url"] == module.ACTELION_SALES_URL
