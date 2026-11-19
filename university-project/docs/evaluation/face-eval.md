# Face matching: measured on a folder of images
4 people, 10 photos, 21 same-person pairs, 24 different-person pairs.

**Read this first.** This is a small sample, so the error rates are rough. The tier thresholds in `app/face.py` are conservative starting points taken from the model's published operating point. Rerun this with a few hundred people before trusting them for real customers. Drawn occlusions are crude stand-ins for a beard, a cap or glasses.
- Same-person distances: 0.049 to 0.387.
- Different-person distances: 0.694 to 0.895.

| Tier floor | Distance | False-reject rate | False-accept rate |
|---|---|---|---|
| match (score 80) | 0.491 | 0.0% | 0.0% |
| likely (score 60) | 0.54 | 0.0% | 0.0% |
| uncertain (score 30) | 0.602 | 0.0% | 0.0% |

Equal error rate about 0.0% at distance 0.387.

## What changing a photo does to the result

| Person | Change | Outcome | Score |
|---|---|---|---|
| alex | darker (x0.55) | match | 100.0 |
| alex | brighter (x1.5) | match | 100.0 |
| alex | lower contrast | match | 100.0 |
| alex | blur (radius 1.5) | match | 100.0 |
| alex | noise (sd 16) | match | 99.9 |
| alex | grayscale | match | 100.0 |
| alex | rotated 10 degrees | match | 100.0 |
| alex | heavy compression | match | 100.0 |
| alex | cap brim drawn on | match | 97.4 |
| alex | lower face covered (stand-in for beard or mask) | match | 99.7 |
| alex | band over the eyes | no face found | None |
| biden | darker (x0.55) | match | 100.0 |
| biden | brighter (x1.5) | match | 100.0 |
| biden | lower contrast | match | 100.0 |
| biden | blur (radius 1.5) | match | 100.0 |
| biden | noise (sd 16) | match | 100.0 |
| biden | grayscale | match | 100.0 |
| biden | rotated 10 degrees | match | 100.0 |
| biden | heavy compression | match | 100.0 |
| biden | cap brim drawn on | match | 99.7 |
| biden | lower face covered (stand-in for beard or mask) | match | 99.2 |
| biden | band over the eyes | no face found | None |
| lin | darker (x0.55) | match | 100.0 |
| lin | brighter (x1.5) | match | 100.0 |
| lin | lower contrast | match | 100.0 |
| lin | blur (radius 1.5) | match | 100.0 |
| lin | noise (sd 16) | match | 100.0 |
| lin | grayscale | match | 100.0 |
| lin | rotated 10 degrees | match | 100.0 |
| lin | heavy compression | match | 100.0 |
| lin | cap brim drawn on | match | 99.9 |
| lin | lower face covered (stand-in for beard or mask) | match | 98.5 |
| lin | band over the eyes | match | 98.7 |
| obama | darker (x0.55) | match | 100.0 |
| obama | brighter (x1.5) | match | 99.9 |
| obama | lower contrast | match | 100.0 |
| obama | blur (radius 1.5) | match | 100.0 |
| obama | noise (sd 16) | match | 100.0 |
| obama | grayscale | match | 100.0 |
| obama | rotated 10 degrees | match | 100.0 |
| obama | heavy compression | match | 100.0 |
| obama | cap brim drawn on | match | 99.7 |
| obama | lower face covered (stand-in for beard or mask) | match | 99.2 |
| obama | band over the eyes | no face found | None |
