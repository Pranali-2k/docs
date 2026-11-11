"""Feed every sample email through the workflow and print expected vs actual outcomes.

    python -m scripts.run_demo
Used for the PRD's "Future Validation & Demo" step before showing it to scheduling staff.
"""
from app.db import SessionLocal, migrate_to_head
from app.demo_samples import SAMPLES, render
from app.seed import seed
from app.services import email_gateway, pipeline


def main() -> None:
    migrate_to_head()
    with SessionLocal() as db:
        seed(db, demo=True)
        print(f"{'sample':24} {'status':15} {'intent':17} {'conf':>5}  reasons / action")
        print("-" * 110)
        for key in SAMPLES:
            s = render(db, key)
            req, _ = email_gateway.ingest_email(db, from_address=s["from_address"], from_name=s["from_name"],
                                                subject=s["subject"], body=s["body"], source="demo")
            pipeline.process_request(db, req, actor="ai-assistant")
            db.commit()
            reasons = ", ".join(r["code"] for r in req.review_reasons) or (req.resolution_note or "auto response")
            print(f"{key:24} {req.status:15} {req.intent or '':17} {req.ai_confidence or 0:5.2f}  {reasons}")
            print(f"{'':24} expected: {s['expected']}")


if __name__ == "__main__":
    main()
