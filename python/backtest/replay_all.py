"""Runner sekuensial: jalankan replay V1 -> V2 -> V3 (satu per satu agar
tidak membebani provider LLM dengan konkurensi).

Pemakaian (background):
  python -u -m backtest.replay_all
"""
import os
import subprocess
import sys

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

VARIANTS = [
    ("v1_base", "prompts/decision.md"),
    ("v2_action", "prompts/decision_v2_action.md"),
    ("v3_edge", "prompts/decision_v3_edge.md"),
]


def main():
    for tag, prompt in VARIANTS:
        print(f"\n===== {tag} ({prompt}) =====", flush=True)
        r = subprocess.run(
            [sys.executable, "-u", "-m", "backtest.llm_replay",
             "--symbol", "XAUUSD", "--prompt", prompt,
             "--bars", "15000", "--step", "30", "--max-tokens", "2048"],
            cwd=_BASE)
        print(f"===== {tag} exit={r.returncode} =====", flush=True)
    print("\nSEMUA VARIAN SELESAI", flush=True)


if __name__ == "__main__":
    main()
