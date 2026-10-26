"""Profile a full two-hour Performance cache using synthetic data only.

Run: uv run python scripts/dev/profile_performance.py --gpus 8 --max-points 1000
"""
from __future__ import annotations

import argparse
import cProfile
import json
import math
import os
import random
import statistics
import time
from array import array
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--gpus', type=int, default=8)
    parser.add_argument('--samples', type=int, default=14400)
    parser.add_argument('--max-points', type=int, default=1000)
    parser.add_argument('--repeat', type=int, default=3)
    parser.add_argument('--profile', type=Path, help='Optional cProfile output file')
    args = parser.parse_args()
    if not (1 <= args.gpus <= 32 and 1 <= args.samples <= 14400 and 1 <= args.repeat <= 20):
        parser.error('Use 1–32 GPUs, 1–14400 samples, and 1–20 repeats')
    if os.environ.get('CONSTELLA_DB_PATH'):
        parser.error('Unset CONSTELLA_DB_PATH: this benchmark must never open a database')

    from fastapi.testclient import TestClient

    from constella.app import create_app
    from constella.cluster import ClusterState
    from constella.highres import HighresGpuCache, performance_curves
    from constella.performance import NVIDIA_GPM_METRICS, NVIDIA_GPM_PROFILE
    from constella.performance_highres import NvidiaGpmSampleRing

    cache = HighresGpuCache()
    now = time.time()
    rng = random.Random(77)
    for gpu in range(args.gpus):
        ring = NvidiaGpmSampleRing(args.samples)
        ring.timestamps = array('d', (now - (args.samples - 1 - i) * .5 for i in range(args.samples)))
        ring.valid_masks = array('H', (((1 << len(NVIDIA_GPM_METRICS)) - 1) if i % 137 else 0
                                       for i in range(args.samples)))
        for metric in NVIDIA_GPM_METRICS:
            scale = 1024 * 1024 if 'per_second' in metric else 1
            ring.values[metric] = array('f', ((35 + 30 * math.sin(i / 19 + gpu) + rng.random() * 25) * scale
                                              for i in range(args.samples)))
        ring.count = args.samples
        key = ('benchmark', f'GPU-{gpu}')
        cache.performance.rings[key] = ring
        cache.performance.devices[key] = {
            'node_id': key[0], 'gpu_uuid': key[1], 'gpu_index': gpu,
            'name': 'Synthetic GPU', 'status': 'available',
        }
    app = create_app(cluster_state=ClusterState(local_node_id='benchmark'), highres_cache=cache)
    assert app.state.db_sink is None
    client = TestClient(app)
    results = []
    for seconds in (300, 900, 7200):
        query_ms, api_ms = [], []
        params = {'node_id': 'benchmark', 'since': now - seconds, 'until': now,
                  'max_points': args.max_points}
        for _ in range(args.repeat):
            start = time.perf_counter()
            performance_curves(cache, node_id='benchmark', since=now - seconds, until=now,
                               max_points=args.max_points, now=now)
            query_ms.append((time.perf_counter() - start) * 1000)
            start = time.perf_counter()
            response = client.get('/api/highres/performance', params=params, headers={'Accept-Encoding': 'gzip'})
            response.raise_for_status()
            api_ms.append((time.perf_counter() - start) * 1000)
        payload = response.json()
        assert payload['profile'] == NVIDIA_GPM_PROFILE
        results.append({
            'range_seconds': seconds, 'gpus': args.gpus, 'samples_per_gpu': args.samples,
            'max_points_per_series': args.max_points,
            'query_ms': round(statistics.median(query_ms), 2),
            'api_ms': round(statistics.median(api_ms), 2),
            'json_bytes': len(response.content),
            'wire_bytes': int(response.headers['content-length']),
            'content_encoding': response.headers.get('content-encoding', 'identity'),
        })
    print(json.dumps(results, indent=2))
    if args.profile:
        profile = cProfile.Profile()
        profile.runcall(performance_curves, cache, node_id='benchmark', since=now - 7200,
                        until=now, max_points=args.max_points, now=now)
        profile.dump_stats(str(args.profile))


if __name__ == '__main__':
    main()
