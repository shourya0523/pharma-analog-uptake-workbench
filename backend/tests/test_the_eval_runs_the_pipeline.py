"""One eval must run the pipeline, and one source ranking must be the pipeline's.

Both rules are here because both were broken at once, silently, and the second
was invisible until someone read two files side by side.

``eval_coverage.py`` calls the readers directly. Checked by AST it touches none
of the twelve stages in ``run_job`` and no LLM entry point, so every figure it
reports is the deterministic extraction floor and its "wrong values" are
figures a reader *emitted*, with the evidence judge and conflict reconciliation
still standing between them and anything published. That is a useful number and
it was being read as the pipeline's accuracy, which was unmeasured.

The defence is not a comment. It is that some eval actually drives
``PipelineOrchestrator.run_job``, so the thing being scored is the product.
"""

from __future__ import annotations

import ast
import pathlib

REPO = pathlib.Path(__file__).resolve().parents[2]
SCRIPTS = REPO / "scripts"
ORCHESTRATOR = REPO / "backend" / "app" / "pipeline" / "orchestrator.py"


def _calls(tree: ast.AST) -> set[str]:
    return {
        node.func.id if isinstance(node.func, ast.Name) else node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, (ast.Name, ast.Attribute))
    }


def test_some_eval_drives_the_orchestrator_itself():
    """An eval that re-implements the pipeline can drift from it, and did.

    Four things drifted at once while every coverage figure kept being quoted
    as the pipeline's: the LLM extractor, the evidence judge, reconciliation
    and the search fallback were all absent from the measurement.
    """
    drivers = [
        path.name
        for path in SCRIPTS.rglob("*.py")
        if "run_job" in _calls(ast.parse(path.read_text()))
    ]
    assert drivers, (
        "no script calls PipelineOrchestrator.run_job, so nothing measures the "
        "pipeline. Every coverage figure is then the deterministic reader "
        "floor, which is a different claim and has been mistaken for this one."
    )


def test_the_end_to_end_eval_scores_what_the_pipeline_published():
    """Reading the datapoints back is the point; counting candidates is not.

    A datapoint the pipeline holds for review is a gap, not an answer, so an
    eval that scores every datapoint it can find credits the pipeline for
    figures it declined to stand behind.
    """
    source = (SCRIPTS / "eval_pipeline_end_to_end.py").read_text()
    tree = ast.parse(source)
    names = _calls(tree)
    assert "run_job" in names
    assert "ValidationStatus" in {
        node.id for node in ast.walk(tree) if isinstance(node, ast.Name)
    }, "published-ness must be defined against the pipeline's own enum"
    assert "DatapointORM" in source, "the score comes from persisted datapoints"


def test_only_the_pipeline_ranks_sources():
    """Two rankings that disagree is one ranking and one bug.

    ``scripts/eval_coverage.py`` held a private table keyed by filing type that
    put the 8-K earnings exhibit above the 10-K, while ``SOURCE_PRIORITY`` puts
    SEC_FILING above EARNINGS_RELEASE. The eval and the pipeline therefore
    resolved a disagreement between two candidates in opposite directions, and
    the eval was the one being quoted.
    """
    assert "SOURCE_PRIORITY = [" in ORCHESTRATOR.read_text()
    offenders = []
    for path in SCRIPTS.rglob("*.py"):
        # A literal dict is the offence; ranking off SOURCE_PRIORITY is a
        # comprehension over it and never trips this. Skipping a file that
        # merely mentions the name would let a comment about the rule stand in
        # for following it, which is how the first version of this test passed
        # against a file that had the private table back in it.
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            # A literal mapping from filing type or source type to a rank is a
            # second ranking however it is spelled.
            if not isinstance(node, ast.Dict):
                continue
            keys = [k.value for k in node.keys
                    if isinstance(k, ast.Constant) and isinstance(k.value, str)]
            values = [v.value for v in node.values
                      if isinstance(v, ast.Constant) and isinstance(v.value, int)]
            if len(keys) < 2 or len(values) != len(node.values):
                continue
            if any(key.upper() in {"8-K", "10-K", "10-Q", "20-F", "6-K"}
                   or key in {"sec_filing", "earnings_release"} for key in keys):
                offenders.append(f"{path.relative_to(REPO)}:{node.lineno}")
    assert not offenders, (
        "these rank sources without using the pipeline's SOURCE_PRIORITY:\n  "
        + "\n  ".join(offenders)
        + "\nImport it from app.pipeline.orchestrator instead of restating it."
    )


def _eval_module():
    """The end-to-end eval, imported the way it imports itself."""
    import importlib.util
    import sys

    for path in (str(REPO / "scripts"), str(REPO / "backend")):
        if path not in sys.path:
            sys.path.insert(0, path)
    spec = importlib.util.spec_from_file_location(
        "eval_pipeline_end_to_end", SCRIPTS / "eval_pipeline_end_to_end.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_a_region_is_not_the_worldwide_total_and_nothing_else_is_claimed():
    """Three vocabularies share `revenue_scope`, and only one pair is comparable.

    Gold says what the issuer's line covers - "U.S." for a product sold
    essentially only there, "Worldwide" for one broken out by region and in
    total. The deterministic readers say granularity, "Product family" or
    "Formulation-specific", and name no geography. The LLM extractor names a
    geography.

    A first version of this rule required a region to match the same region,
    which is coherent and wrong: it scored at zero every product whose gold row
    says "U.S." while the reader that answered it says "Product family".
    Re-scoring the stored runs caught it before it was ever reported. The rule
    that survived asserts one thing only.
    """
    answers_scope = _eval_module().answers_scope

    # The case that needs separating: a filer prints a product by region and
    # in total, and the U.S. line is close enough to the worldwide one to pass
    # for it.
    assert not answers_scope("U.S.", "Worldwide")
    assert not answers_scope("Europe", "Worldwide")
    assert not answers_scope("Other International", "Worldwide")

    # A granularity label names no geography, so it answers whatever is asked.
    assert answers_scope("Product family", "Worldwide")
    assert answers_scope("Product family", "U.S.")
    assert answers_scope("Formulation-specific", "U.S.")
    assert answers_scope("Formulation-specific", "Product family")
    assert answers_scope(None, "Worldwide")

    # A gold row that is not worldwide asserts nothing about regions here.
    assert answers_scope("U.S.", "U.S.")
    assert answers_scope("U.S.", "Product family")


def test_a_held_answer_outranks_a_published_one_from_another_series():
    """Reporting order: what the pipeline did about *this* series comes first."""
    module = _eval_module()
    held = [{"value": 768.0, "status": "needs_review"}]
    off = [{"value": 744.0, "status": "auto_pass"}]

    assert module.scored_state([], held, 768.0, off)[0] == "held_correct"
    assert module.scored_state([], [], 768.0, off)[0] == "published_other_scope"
    assert module.scored_state([], [], 768.0, [])[0] == "no_datapoint"


def test_reading_order_and_authority_are_separate_questions():
    """Two rankings, opposite answers, both correct about their own question.

    `SOURCE_PRIORITY` ranks authority — which figure wins when two disagree —
    and puts the audited 10-K above an 8-K press exhibit. `DOCUMENT_FITNESS`
    ranks where to look for a product's quarterly sales, and puts the 8-K item
    2.02 exhibit first, because it is a product-sales schedule while a 10-K
    names a product across dozens of tables for other reasons.

    Ordering the read by authority is what the eval did for one commit. It was
    invisible while the connector ignored the date window and fetched primary
    filings that covered the wrong years; once they covered the right ones, a
    10-K's incidental table began answering quarters before the schedule built
    to state them, and rows that had read correctly stopped being found.
    """
    from app.domain.models import SourceType
    from app.pipeline.orchestrator import SOURCE_PRIORITY, reading_rank

    authority = {t: i for i, t in enumerate(SOURCE_PRIORITY)}

    assert authority[SourceType.SEC_FILING] < authority[SourceType.EARNINGS_RELEASE]
    assert reading_rank(SourceType.EARNINGS_RELEASE) < reading_rank(SourceType.SEC_FILING)
    assert reading_rank("earnings_release") < reading_rank("sec_filing")
    assert reading_rank("something_else") == reading_rank(SourceType.OTHER) + 1


def test_the_coverage_eval_orders_documents_by_fitness_not_authority():
    """The eval must not restate either ranking, and must pick the right one."""
    tree = ast.parse((SCRIPTS / "eval_coverage.py").read_text())
    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    assert "reading_rank" in imported
    # Checked against the names the code actually uses, not the file's text: a
    # comment explaining why authority is the wrong ranking here mentions it,
    # and an earlier version of this guard was satisfied by exactly that.
    assert "SOURCE_PRIORITY" not in imported | names, (
        "ordering the read by authority is the inversion this guards against"
    )
