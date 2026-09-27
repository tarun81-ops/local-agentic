"""Exercises the Organizer end to end against disposable scratch files: propose -> apply -> undo.
Run: .venv\\Scripts\\python.exe test_organizer.py
"""
import shutil
from pathlib import Path

from app.core.file_ops.file_mover import move, undo_last
from app.core.file_ops.safe_delete import trash
from app.core.llm.organizer import propose_plan

SCRATCH = Path(__file__).resolve().parent / "data" / "organize_scratch"


def make_scratch_files() -> list[dict]:
    if SCRATCH.exists():
        shutil.rmtree(SCRATCH)
    SCRATCH.mkdir(parents=True)

    (SCRATCH / "report.pdf").write_bytes(b"%PDF-1.4 fake report")
    (SCRATCH / "notes.txt.bak").write_text("editor backup file, safe to delete, not needed")
    (SCRATCH / "Invoices").mkdir()
    (SCRATCH / "Invoices" / "vacation_photo.jpg").write_bytes(b"\xff\xd8\xff fake jpeg")

    return [{"path": str(p), "size": p.stat().st_size} for p in SCRATCH.rglob("*") if p.is_file()]


def main():
    files = make_scratch_files()
    print("scratch files:")
    for f in files:
        print(" ", f["path"])

    plan = propose_plan(files)
    print(f"\nplan summary: {plan.summary}")
    print(f"dropped_invalid: {plan.dropped_invalid}")
    for a in plan.actions:
        print(f"  {a.op}: {a.path} -> {a.to}")

    if not plan.actions:
        print("\nno actions proposed, nothing to apply")
        return

    print("\napplying plan...")
    for a in plan.actions:
        if a.op == "move":
            move(Path(a.path), Path(a.to), SCRATCH)
        elif a.op == "delete":
            trash(Path(a.path))
    print("applied. remaining files:", [str(p) for p in SCRATCH.rglob("*") if p.is_file()])

    move_count = sum(1 for a in plan.actions if a.op == "move")
    print(f"\nundoing {move_count} move(s)...")
    for _ in range(move_count):
        undone = undo_last(SCRATCH)
        print("  undid:", undone)


if __name__ == "__main__":
    main()
