"""
evaluation/round5_final_validation.py
──────────────────────────────────────
DEPRECATED — merged into evaluation/run_all.py.

Round 5 added no new checks over R1+R2 and had a broken structural
pass criterion (issues == 0 instead of a % threshold).

Use evaluation/run_all.py instead:
    python -m evaluation.run_all
"""
from evaluation.run_all import main

if __name__ == "__main__":
    main()
