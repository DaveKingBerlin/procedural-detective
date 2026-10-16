"""Phase 44 ADV — default/demo fixture vs Hard Gate 3 contract (real service)."""
import sys, tempfile, os, uuid
from pathlib import Path

REPO = Path(r"C:\Users\Fujitsu\coding\procedural-detective")
for p in (str(REPO / "backend"), str(REPO / "backend" / "tests")):
    if p not in sys.path:
        sys.path.insert(0, p)

import conftest  # noqa: E402
from app.persistence.store import Store  # noqa: E402
from app.services.generation import GenerationService  # noqa: E402
from app.generation.fake_provider import FakeProvider  # noqa: E402
import test_phase44_playability_pipeline as t44p  # noqa: E402

PROMPT = t44p.GOLDEN_PROMPT
for difficulty in ("easy", "medium", "hard"):
    db = Path(tempfile.gettempdir()) / f"pd44adv_h_{difficulty}_{uuid.uuid4().hex}.sqlite"
    url = f"sqlite:///{db.as_posix()}"
    conftest.upgrade_db(url)
    store = Store(url)
    try:
        svc = GenerationService(
            settings=conftest.make_phase5_settings(url),
            store=store,
            provider_factory=lambda: FakeProvider(script=t44p._golden_script()),
        )
        sess = svc.create_anonymous_quota_session()
        res = svc.start_case_generation(
            PROMPT, anonymous_quota_session_id=sess.anonymous_quota_session_id,
            difficulty=difficulty,
        )
        cv = store.get_case_version(res.case_id, 1)
        pub = store.get_published(res.case_id, 1)
        print(f"difficulty={difficulty:6} default fixture -> status={res.status:9} "
              f"case_version={getattr(cv,'state',None)} published_row={pub is not None}")
    finally:
        store.dispose()
        try:
            os.remove(db)
        except OSError:
            pass
