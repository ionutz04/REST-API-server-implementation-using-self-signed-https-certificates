import os
import sys
import time
import subprocess

from push_to_redis import push_csv_to_redis

INTERVAL = int(os.environ.get("INTERVAL_SECONDS", 18000))  # 5 ore


def run_once() -> bool:
    print("=== [scheduler] START run.sh ===", flush=True)
    # -it din run.sh necesită TTY; îl scoatem pe loc fără să modificăm fișierul
    proc = subprocess.run("sed 's/ -it//' ./run.sh | bash", shell=True)
    if proc.returncode != 0:
        print(f"[scheduler] run.sh a eșuat cu codul {proc.returncode}",
              file=sys.stderr, flush=True)
        return False

    print("=== [scheduler] Trimit forecast în Redis ===", flush=True)
    push_csv_to_redis()
    print("=== [scheduler] GATA ===\n", flush=True)
    return True


if __name__ == "__main__":
    # Prima rulare imediată la pornire
    while True:
        try:
            run_once()
        except Exception as exc:
            print(f"[scheduler] EROARE: {exc}", file=sys.stderr, flush=True)

        print(f"[scheduler] Dorm {INTERVAL}s până la următoarea rulare...",
              flush=True)
        time.sleep(INTERVAL)
