"""The member register as a store a run can add to, rather than a file it reads.

The file could not be the store. It lives in the source tree, a deployment runs
several workers over containers that are discarded when the run ends, and the
drugs a run is about arrive at upload time - so a mapping learned while reading
a filing had nowhere to go, and a mapping decided before the upload was decided
against the wrong list.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.migrations import upgrade_database
from app.db.models import DrugJobORM, ExtractionRunORM, XbrlMemberResolutionORM
from app.extraction import member_store
from app.extraction.members import (
    VERDICT_NO_CANDIDATE_MATCH,
    VERDICT_PRODUCT,
    Resolution,
    load_products,
    resolve,
)


def _session(tmp_path: Path) -> Session:
    engine = create_engine(f"sqlite:///{tmp_path / 'workbench.db'}")
    upgrade_database(engine)
    return Session(engine)


def test_the_reviewed_file_seeds_the_table(tmp_path: Path):
    with _session(tmp_path) as db:
        register = member_store.load_register(db)
        assert register, "a fresh database starts where the reviewed file left off"
        assert ("Gilead", "gild:HIVProductsBiktarvyMember") in register
        assert register[("Gilead", "gild:HIVProductsBiktarvyMember")].product == "Biktarvy"


def test_seeding_twice_changes_nothing(tmp_path: Path):
    with _session(tmp_path) as db:
        before = db.scalars(select(XbrlMemberResolutionORM)).all()
        assert member_store.seed_from_csv(db) == 0
        assert len(db.scalars(select(XbrlMemberResolutionORM)).all()) == len(before)


def test_what_a_run_learns_survives_a_reseed(tmp_path: Path):
    """The table leads once it exists. Re-reading the file over the top of it
    would undo both what runs learned and what reviewers settled."""
    with _session(tmp_path) as db:
        member_store.record(db, "Bayer", Resolution(
            "bayr:NubeqaMember", "Nubeqa", "exact", 1.0, "", verdict=VERDICT_PRODUCT))
        db.flush()

        member_store.seed_from_csv(db)

        assert member_store.load_register(db)[("Bayer", "bayr:NubeqaMember")].product == "Nubeqa"


def test_a_reviewer_outranks_anything_automated(tmp_path: Path):
    """The rule the metadata backfill already follows, applied to members."""
    with _session(tmp_path) as db:
        member_store.record(db, "Liquidia", Resolution(
            "us-gaap:ProductMember", "Yutrepia", "human", 1.0, "sole marketed product"))
        db.flush()
        row = db.scalar(select(XbrlMemberResolutionORM).where(
            XbrlMemberResolutionORM.issuer == "Liquidia"))
        row.validation_status = member_store.CONFIRMED
        db.flush()

        changed = member_store.record(db, "Liquidia", Resolution(
            "us-gaap:ProductMember", None, "llm", 1.0, "a total, surely",
            verdict=VERDICT_NO_CANDIDATE_MATCH))

        assert changed is False
        assert member_store.load_register(db)[
            ("Liquidia", "us-gaap:ProductMember")].product == "Yutrepia"


def test_a_negative_is_stored_against_the_list_it_was_judged_against(tmp_path: Path):
    """Storing it without that is what made the old file a standing veto."""
    with _session(tmp_path) as db:
        member_store.record(
            db,
            "Gilead",
            Resolution("gild:SomethingMember", None, "unmatched", 0.0, "no match"),
            products=["Biktarvy", "Descovy"],
        )
        db.flush()

        row = db.scalar(select(XbrlMemberResolutionORM).where(
            XbrlMemberResolutionORM.member == "gild:SomethingMember"))
        assert row.verdict == VERDICT_NO_CANDIDATE_MATCH
        assert row.candidates_fingerprint

        entry = member_store.load_register(db)[("Gilead", "gild:SomethingMember")]
        assert entry.binds(["Biktarvy", "Descovy"])
        assert not entry.binds(["Biktarvy", "Descovy", "Trodelvy"])


def test_a_drug_uploaded_at_run_time_is_read_from_the_seeded_register(tmp_path: Path):
    """End to end on the real seed file, which is where the defect lived.

    Trodelvy is a Gilead product that `product_attributes.csv` does not track,
    so the register carries `gild:TrodelvyMember` as naming no product. Upload
    Trodelvy and that answer has to stop applying, or the pipeline skips a fact
    the filer tagged and the rules place immediately.
    """
    with _session(tmp_path) as db:
        register = member_store.load_register(db)
        seeded = register[("Gilead", "gild:TrodelvyMember")]
        assert seeded.product is None and seeded.candidates_fingerprint

        tracked = load_products()
        assert "Trodelvy" not in tracked

        assert resolve("gild:TrodelvyMember", tracked, register, issuer="Gilead").product is None
        uploaded = resolve("gild:TrodelvyMember", [*tracked, "Trodelvy"],
                           register, issuer="Gilead")
        assert uploaded.product == "Trodelvy"


def test_the_run_contributes_its_own_uploads_to_the_candidate_list(tmp_path: Path):
    with _session(tmp_path) as db:
        db.add(ExtractionRunORM(id="run-1", status="running", options_json={}))
        db.add_all([
            DrugJobORM(id="job-1", run_id="run-1", drug_name="Trodelvy"),
            DrugJobORM(id="job-2", run_id="run-1", drug_name="Veklury"),
            DrugJobORM(id="job-3", run_id="run-2", drug_name="Elsewhere"),
        ])
        db.flush()

        assert member_store.run_products(db, "run-1") == ["Trodelvy", "Veklury"]


def test_the_measured_corpus_sees_exactly_what_it_saw_before():
    """The fix changes answers only for lists the register never judged.

    Every seeded negative was decided against the products in
    `product_attributes.csv`, which is also the list the coverage eval resolves
    against - so for that list every stored decision still binds and the score
    is untouched. A change here is a change to what was measured, and should be
    argued for rather than discovered.
    """
    from app.extraction.members import load_register

    register = load_register()
    tracked = load_products()
    for (issuer, member), entry in register.items():
        assert resolve(member, tracked, register, issuer=issuer) is entry


def test_a_failed_member_write_does_not_cost_the_job_its_work(tmp_path: Path, monkeypatch):
    """Bookkeeping must not be able to roll back an extraction.

    These writes happen inside the session that is holding the job's sources
    and datapoints, so the duplicate-member case that two workers can reach
    together is handled on a savepoint. A plain rollback would take the job's
    own rows with it - a far worse outcome than losing a mapping another worker
    has already written identically.
    """
    with _session(tmp_path) as db:
        db.add(ExtractionRunORM(id="run-1", status="running", options_json={}))
        db.add(DrugJobORM(id="job-1", run_id="run-1", drug_name="Nubeqa"))
        db.flush()

        def _lost_the_race(*_args, **_kwargs):
            raise IntegrityError("INSERT", {}, Exception("duplicate member"))

        monkeypatch.setattr(member_store, "record", _lost_the_race)
        written = member_store.record_many(db, {("Bayer", "bayr:NubeqaMember"): Resolution(
            "bayr:NubeqaMember", "Nubeqa", "exact", 1.0, "", verdict=VERDICT_PRODUCT)})

        assert written == 0
        db.commit()
        assert db.get(DrugJobORM, "job-1") is not None
