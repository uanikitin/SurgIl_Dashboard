# scratch_check_reagents.py

from backend.db import SessionLocal
from backend.models.reagents import ReagentSupply

def main():
    db = SessionLocal()
    try:
        # просто посчитать строки
        count = db.query(ReagentSupply).count()
        print(f"В таблице reagent_supplies сейчас строк: {count}")

        # показать пару строк (если есть)
        supplies = db.query(ReagentSupply).order_by(ReagentSupply.id.desc()).limit(5).all()
        for s in supplies:
            print(
                f"[{s.id}] {s.received_at} | {s.reagent} | {s.qty} {s.unit} "
                f"({s.source or '-'} / {s.location or '-'})"
            )
    finally:
        db.close()

if __name__ == "__main__":
    main()