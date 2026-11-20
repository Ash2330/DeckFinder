"""Upload a labelled deck corpus and measure DeckFinder through its public API."""
import argparse
import json
import math
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from statistics import median
from time import perf_counter, sleep

import requests


def request(method, url, token, **kwargs):
    response = requests.request(method, url, headers={"Authorization": "Bearer " + token},
                                timeout=90, **kwargs)
    response.raise_for_status()
    return response.json()


def p95(values):
    return round(sorted(values)[math.ceil(len(values) * 0.95) - 1], 1) if values else None


def benchmark(args):
    teacher = os.environ["DECKFINDER_TEACHER_TOKEN"]
    student = os.environ["DECKFINDER_STUDENT_TOKEN"]
    base = args.url.rstrip("/") + "/api"
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    ids = {}
    accepted = {}
    ready = {}
    failed = set()
    during = []

    def upload(deck):
        path = args.manifest.parent / deck["file"]
        with path.open("rb") as stream:
            result = request("POST", base + "/upload", teacher, files={"file": (path.name, stream)},
                             data={"title": deck["title"], "course": deck.get("course", "")})
        return deck["file"], result["id"], perf_counter()

    def search(case, mode):
        start = perf_counter()
        result = request("GET", base + "/search", student, params={"q": case["q"], "mode": mode})
        return [deck["id"] for deck in result], (perf_counter() - start) * 1000

    cases = manifest["queries"]
    if not cases or not manifest["decks"]:
        raise ValueError("Include at least one deck and one labelled query.")
    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        uploads = [pool.submit(upload, deck) for deck in manifest["decks"]]
        deadline = perf_counter() + args.timeout
        while uploads or len(ready) + len(failed) < len(accepted):
            if perf_counter() >= deadline:
                raise TimeoutError("Decks did not finish indexing before the deadline.")
            done = [future for future in uploads if future.done()]
            for future in done:
                filename, deck_id, timestamp = future.result()
                ids[filename] = deck_id
                accepted[deck_id] = timestamp
                uploads.remove(future)
            if accepted:
                library = request("GET", base + "/library", teacher)
                checked = perf_counter()
                for row in library:
                    deck_id = row["id"]
                    if deck_id in accepted and row["status"] == "ready":
                        ready.setdefault(deck_id, (checked - accepted[deck_id]) * 1000)
                    if deck_id in accepted and row["status"] == "failed":
                        failed.add(deck_id)
            if uploads or len(ready) + len(failed) < len(accepted):
                _, duration = search(cases[len(during) % len(cases)], "hybrid")
                during.append(duration)
                sleep(1)

    modes = {}
    for mode in ("hybrid", "keyword", "vector"):
        hits = []
        latencies = []
        for case in cases:
            ranked, duration = search(case, mode)
            hits.append(ids[case["expected_file"]] in ranked[:5])
            latencies.append(duration)
        modes[mode] = {"hit_at_5": sum(hits) / len(hits), "p95_ms": p95(latencies)}

    if ready:
        deck_id = next(iter(ready))
        link = request("GET", f"{base}/decks/{deck_id}/download", student)
        with requests.get(link["url"], stream=True, timeout=30) as response:
            response.raise_for_status()
            if not next(response.iter_content(4), b"").startswith(b"PK"):
                raise ValueError("The downloaded file does not have a PowerPoint ZIP signature.")

    print(json.dumps({
        "uploaded": len(accepted), "indexed": len(ready), "failed": len(failed),
        "observed_index_p50_ms": round(median(ready.values()), 1) if ready else None,
        "observed_index_p95_ms": p95(list(ready.values())),
        "hybrid_during_indexing_samples": len(during),
        "hybrid_during_indexing_p95_ms": p95(during),
        "search_after_indexing": modes,
    }, indent=2))
    return bool(failed)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url")
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--timeout", type=int, default=1800)
    raise SystemExit(benchmark(parser.parse_args()))
