"""The member register as a store a run can add to, rather than a file it reads.

The file cannot be the store: it lives in the source tree, a deployment runs
several workers over containers that are discarded when the run ends, and the
products a run is about arrive at upload time. So a mapping learned while
reading a filing has nowhere to go, and a mapping decided before the upload was
decided against a list that does not include it.

Invented names for everything constructed here. Where a test is about the
seeded register itself, the member is derived from it rather than spelled.
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
    match,
    resolve,
    words,
)


def _session(tmp_path: Path) -> Session:
    engine = create_engine(f"sqlite:///{tmp_path / 'workbench.db'}")
    upgrade_database(engine)
    return Session(engine)


def test_the_reviewed_file_seeds_the_table(tmp_path: Path):
    with _session(tmp_path) as db:
        register = member_store.load_register(db)

        assert register, "a fresh database starts where the reviewed file left off"
        assert any(entry.resolved for entry in register.values())
        assert all(issuer and member for issuer, member in register)


def test_seeding_twice_changes_nothing(tmp_path: Path):
    with _session(tmp_path) as db:
        before = db.scalars(select(XbrlMemberResolutionORM)).all()
        assert member_store.seed_from_csv(db) == 0
        assert len(db.scalars(select(XbrlMemberResolutionORM)).all()) == len(before)


def test_what_a_run_learns_survives_a_reseed(tmp_path: Path):
    """The table leads once it exists. Re-reading the file over the top of it
    would undo both what runs learned and what reviewers settled."""
    with _session(tmp_path) as db:
        member_store.record(db, "Acme Pharma", Resolution(
            "acme:CalderonMember", "Calderon", "exact", 1.0, "", verdict=VERDICT_PRODUCT))
        db.flush()

        member_store.seed_from_csv(db)

        learned = member_store.load_register(db)[("Acme Pharma", "acme:CalderonMember")]
        assert learned.product == "Calderon"


def test_a_reviewer_outranks_anything_automated(tmp_path: Path):
    """The rule the metadata backfill already follows, applied to members."""
    with _session(tmp_path) as db:
        member_store.record(db, "Solo Biosciences", Resolution(
            "us-gaap:ProductMember", "Monovex", "human", 1.0, "sole marketed product"))
        db.flush()
        row = db.scalar(select(XbrlMemberResolutionORM).where(
            XbrlMemberResolutionORM.issuer == "Solo Biosciences"))
        row.validation_status = member_store.CONFIRMED
        db.flush()

        changed = member_store.record(db, "Solo Biosciences", Resolution(
            "us-gaap:ProductMember", None, "llm", 1.0, "a total, surely",
            verdict=VERDICT_NO_CANDIDATE_MATCH))

        assert changed is False
        assert member_store.load_register(db)[
            ("Solo Biosciences", "us-gaap:ProductMember")].product == "Monovex"


def test_a_negative_is_stored_against_the_list_it_was_judged_against(tmp_path: Path):
    """A negative stored without it is a veto against every list, not one."""
    with _session(tmp_path) as db:
        member_store.record(
            db,
            "Acme Pharma",
            Resolution("acme:SomethingMember", None, "unmatched", 0.0, "no match"),
            products=["Veltrexa", "Cordexa"],
        )
        db.flush()

        row = db.scalar(select(XbrlMemberResolutionORM).where(
            XbrlMemberResolutionORM.member == "acme:SomethingMember"))
        assert row.verdict == VERDICT_NO_CANDIDATE_MATCH
        assert row.candidates_fingerprint

        entry = member_store.load_register(db)[("Acme Pharma", "acme:SomethingMember")]
        assert entry.binds(["Veltrexa", "Cordexa"])
        assert not entry.binds(["Veltrexa", "Cordexa", "Calderon"])


def test_a_product_uploaded_at_run_time_is_read_from_the_seeded_register(tmp_path: Path):
    """The same rule end to end on the seeded register rather than a fixture.

    The member is derived, not named: any member the register records as
    matching nothing, whose own trailing words are a product the rules would
    place if it were on the list. That is the shape of a product a run uploads
    and the catalogue does not carry, and the register has to stop answering
    for it.
    """
    tracked = load_products()
    lowered = {product.lower() for product in tracked}

    with _session(tmp_path) as db:
        register = member_store.load_register(db)

        case = None
        for (issuer, member), entry in sorted(register.items()):
            if entry.resolved or not entry.candidates_fingerprint:
                continue
            uploaded = " ".join(words(member)[-1:])
            if not uploaded or uploaded in lowered:
                continue
            if match(member, [*tracked, uploaded]).product == uploaded:
                case = (issuer, member, uploaded)
                break

        assert case, "the register holds no member of the shape this is about"
        issuer, member, uploaded = case

        assert resolve(member, tracked, register, issuer=issuer).product is None
        assert resolve(member, [*tracked, uploaded], register,
                       issuer=issuer).product == uploaded


def test_the_run_contributes_its_own_uploads_to_the_candidate_list(tmp_path: Path):
    with _session(tmp_path) as db:
        db.add(ExtractionRunORM(id="run-1", status="running", options_json={}))
        db.add_all([
            DrugJobORM(id="job-1", run_id="run-1", drug_name="Calderon"),
            DrugJobORM(id="job-2", run_id="run-1", drug_name="Veltrexa"),
            DrugJobORM(id="job-3", run_id="run-2", drug_name="Elsewhere"),
        ])
        db.flush()

        assert member_store.run_products(db, "run-1") == ["Calderon", "Veltrexa"]


def test_the_measured_corpus_sees_exactly_what_it_saw_before():
    """Against the list it was judged on, the register answers as it always did.

    Every seeded negative was decided against `product_attributes.csv`, so for
    that list every stored decision still binds and nothing resolves
    differently. A resolution that changes here changes what the pipeline reads
    for products it already tracks, which is a thing to argue for rather than
    to discover.
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
        db.add(DrugJobORM(id="job-1", run_id="run-1", drug_name="Calderon"))
        db.flush()

        def _lost_the_race(*_args, **_kwargs):
            raise IntegrityError("INSERT", {}, Exception("duplicate member"))

        monkeypatch.setattr(member_store, "record", _lost_the_race)
        written = member_store.record_many(db, {("Acme Pharma", "acme:CalderonMember"): Resolution(
            "acme:CalderonMember", "Calderon", "exact", 1.0, "", verdict=VERDICT_PRODUCT)})

        assert written == 0
        db.commit()
        assert db.get(DrugJobORM, "job-1") is not None
