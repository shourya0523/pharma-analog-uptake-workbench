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
