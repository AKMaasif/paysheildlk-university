"""python -m evaluation.face_eval --images DIR [--out ../docs/evaluation]

DIR holds one folder per person, each with that person's photos:  DIR/alice/1.jpg, DIR/alice/2.jpg, DIR/bob/1.jpg ...

It measures, on YOUR images:
  * genuine distances (two photos of the same person) and impostor distances (photos of different people)
  * false accepts and false rejects at each tier threshold, and the equal error rate
  * how much the score moves when a photo is changed: darker, brighter, blurred, noisy, compressed, rotated,
    and with a cap brim, a covered lower face (a stand-in for a beard or mask) or a band over the eyes drawn on

Read the limits before using any number from it:
  * Drawn occlusions are crude stand-ins. They are not a real beard, haircut, cap or pair of glasses.
  * Two photos of a person are not the same as two scans of that person on a phone months apart.
  * A handful of people gives very rough error rates. Use a few hundred people with several photos each before
    choosing thresholds for real customers.
"""

import argparse
import io
import itertools
import json
import math
from pathlib import Path

from app import face


def _load(folder: Path) -> dict[str, list]:
    from PIL import Image

    engine = face.get_engine()
    people: dict[str, list] = {}
    for person in sorted(p for p in folder.iterdir() if p.is_dir()):
        for path in sorted(person.glob("*")):
            if path.suffix.lower() not in {".jpg", ".jpeg", ".png"}:
                continue
            buf = io.BytesIO()
            Image.open(path).convert("RGB").save(buf, "JPEG", quality=92)
            o = engine.observe(buf.getvalue(), want_embedding=True)
            if o.faces == 1 and o.embedding:
                people.setdefault(person.name, []).append((path.name, o.embedding, buf.getvalue()))
    return people


def _rates(genuine: list[float], impostor: list[float], threshold: float) -> dict:
    return {
        "threshold_distance": round(threshold, 3), "score": face.similarity(threshold),
        "false_reject_rate": round(sum(d > threshold for d in genuine) / len(genuine), 4) if genuine else None,
        "false_accept_rate": round(sum(d <= threshold for d in impostor) / len(impostor), 4) if impostor else None,
    }


def _eer(genuine, impostor):
    best = None
    for t in sorted(set(genuine + impostor)):
        frr = sum(d > t for d in genuine) / len(genuine)
        far = sum(d <= t for d in impostor) / len(impostor)
        gap = abs(frr - far)
        if best is None or gap < best[0]:
            best = (gap, t, (frr + far) / 2)
    return {"threshold_distance": round(best[1], 3), "rate": round(best[2], 4)} if best else None


def _perturbations(jpeg: bytes):
    import numpy as np
    from PIL import Image, ImageDraw, ImageEnhance, ImageFilter

    base = Image.open(io.BytesIO(jpeg)).convert("RGB")
    arr = np.array(base)
    import dlib

    rect = dlib.get_frontal_face_detector()(arr, 1)
    out = {
        "darker (x0.55)": ImageEnhance.Brightness(base).enhance(0.55),
        "brighter (x1.5)": ImageEnhance.Brightness(base).enhance(1.5),
        "lower contrast": ImageEnhance.Contrast(base).enhance(0.55),
        "blur (radius 1.5)": base.filter(ImageFilter.GaussianBlur(1.5)),
        "noise (sd 16)": Image.fromarray(np.clip(arr + np.random.RandomState(0).normal(0, 16, arr.shape), 0, 255).astype("uint8")),
        "grayscale": base.convert("L").convert("RGB"),
        "rotated 10 degrees": base.rotate(10, expand=True),
        "heavy compression": Image.open(io.BytesIO(_jpeg(base, 15))),
    }
    if rect:
        r = rect[0]
        l, t, rr, b = r.left(), r.top(), r.right(), r.bottom()
        h = b - t
        cap = base.copy(); ImageDraw.Draw(cap).rectangle([l - 10, t - 20, rr + 10, t + int(0.18 * h)], fill=(30, 30, 40)); out["cap brim drawn on"] = cap
        low = base.copy(); ImageDraw.Draw(low).rectangle([l, t + int(0.62 * h), rr, b + 5], fill=(60, 40, 30)); out["lower face covered (stand-in for beard or mask)"] = low
        eyes = base.copy(); ImageDraw.Draw(eyes).rectangle([l, t + int(0.30 * h), rr, t + int(0.48 * h)], fill=(10, 10, 10)); out["band over the eyes"] = eyes
    return out


def _jpeg(img, quality=90) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=quality)
    return buf.getvalue()


def run(folder: Path) -> dict:
    people = _load(folder)
    genuine, impostor = [], []
    for name, items in people.items():
        genuine += [face._distance(a[1], b[1]) for a, b in itertools.combinations(items, 2)]
    for (n1, i1), (n2, i2) in itertools.combinations(people.items(), 2):
        impostor += [face._distance(a[1], b[1]) for a in i1 for b in i2]
    result = {
        "people": len(people), "photos": sum(len(v) for v in people.values()), "genuine_pairs": len(genuine), "impostor_pairs": len(impostor),
        "genuine": {"min": round(min(genuine), 3), "max": round(max(genuine), 3)} if genuine else None,
        "impostor": {"min": round(min(impostor), 3), "max": round(max(impostor), 3)} if impostor else None,
        "at_thresholds": {},
    }
    for label, score in (("match (score 80)", face.MATCH), ("likely (score 60)", face.LIKELY), ("uncertain (score 30)", face.UNCERTAIN)):
        # distance at which the score equals the tier floor
        d = face.CENTRE + face.WIDTH * math.log((100 - score) / score)
        result["at_thresholds"][label] = _rates(genuine, impostor, d) if genuine and impostor else None
    result["equal_error_rate"] = _eer(genuine, impostor) if genuine and impostor else None

    shifts = []
    for name, items in people.items():
        if not items:
            continue
        ref = [e for _n, e, _j in items]
        for path_name, emb, jpeg in items[:1]:
            for label, image in _perturbations(jpeg).items():
                o = face.get_engine().observe(_jpeg(image), want_embedding=True)
                if o.faces != 1:
                    shifts.append({"person": name, "change": label, "outcome": "no face found", "score": None})
                    continue
                d = min(face._distance(o.embedding, r) for r in ref[1:] or ref) if len(ref) > 1 else face._distance(o.embedding, emb)
                shifts.append({"person": name, "change": label, "outcome": face.tier(face.similarity(d)), "score": face.similarity(d), "distance": round(d, 3)})
    result["changes"] = shifts
    return result


def write(result: dict, out: Path) -> None:
    pct = lambda x: "n/a" if x is None else f"{x * 100:.1f}%"
    md = ["# Face matching: measured on a folder of images\n"]
    md.append(
        f"{result['people']} people, {result['photos']} photos, {result['genuine_pairs']} same-person pairs, {result['impostor_pairs']} different-person pairs.\n\n"
        "**Read this first.** This is a small sample, so the error rates are rough. The tier thresholds in `app/face.py` are conservative starting points taken from the model's published operating point. "
        "Rerun this with a few hundred people before trusting them for real customers. Drawn occlusions are crude stand-ins for a beard, a cap or glasses.\n"
    )
    if result["genuine"] and result["impostor"]:
        md.append(f"- Same-person distances: {result['genuine']['min']} to {result['genuine']['max']}.\n- Different-person distances: {result['impostor']['min']} to {result['impostor']['max']}.\n")
        md.append("\n| Tier floor | Distance | False-reject rate | False-accept rate |\n|---|---|---|---|\n")
        for label, r in result["at_thresholds"].items():
            md.append(f"| {label} | {r['threshold_distance']} | {pct(r['false_reject_rate'])} | {pct(r['false_accept_rate'])} |\n")
        if result["equal_error_rate"]:
            md.append(f"\nEqual error rate about {pct(result['equal_error_rate']['rate'])} at distance {result['equal_error_rate']['threshold_distance']}.\n")
    md.append("\n## What changing a photo does to the result\n\nEach change is compared with the same person's other photos when there are any, and otherwise with the unchanged photo itself, which is the easier test.\n\n| Person | Change | Outcome | Score |\n|---|---|---|---|\n")
    for c in result["changes"]:
        md.append(f"| {c['person']} | {c['change']} | {c['outcome']} | {c.get('score', 'n/a')} |\n")
    out.mkdir(parents=True, exist_ok=True)
    (out / "face-eval.md").write_text("".join(md))
    (out / "face-eval.json").write_text(json.dumps(result, indent=2))


def main() -> None:
    from evaluation import harness

    parser = argparse.ArgumentParser()
    parser.add_argument("--images", required=True)
    parser.add_argument("--out", default="../docs/evaluation")
    args = parser.parse_args()
    harness.boot()
    result = run(Path(args.images))
    write(result, Path(args.out))
    print(f"Wrote {args.out}/face-eval.md: {result['photos']} photos, {result['genuine_pairs']} same-person and {result['impostor_pairs']} different-person pairs.")


if __name__ == "__main__":
    main()
