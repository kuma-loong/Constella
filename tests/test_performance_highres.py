from __future__ import annotations

import math

import pytest
from fastapi.testclient import TestClient

from constella.app import create_app
from constella.cluster import ClusterState
from constella.highres import HighresGpuCache, performance_curves
from constella.highres_sidecar import HighresSidecarConfig, create_highres_sidecar_app
from constella.performance_highres import NvidiaGpmSampleRing

METRICS = ['nvidia.gpm.sm_active', 'nvidia.gpm.tensor_active']


@pytest.mark.parametrize('times', [[], [1, 2, 2, 3], [1, 2, 2, 3, 4, 5, 5, 6], [1, 3, 2, 4]])
@pytest.mark.parametrize(('since', 'until'), [(0, 10), (2, 3), (4, 5), (2.5, 3), (8, 9), (0, 0)])
@pytest.mark.parametrize('summary_only', [False, True])
def test_performance_window_matches_samples_after_wrap_and_clock_changes(times, since, until, summary_only):
    ring = NvidiaGpmSampleRing(capacity=5)
    retained = []
    for i, stamp in enumerate(times):
        available = i % 3 != 1
        ring.append(sampled_at=stamp, performance={
            'status': 'available' if available else 'error',
            'metrics': {metric: i + j + .25 for j, metric in enumerate(METRICS)},
        })
        retained.append((stamp, i, available))
    window = []
    for stamp, i, available in retained[-5:]:
        if stamp < since:
            continue
        if stamp > until:
            break
        window.append((stamp, i, available))
    result = ring.metric_series_many(METRICS, since=since, until=until, max_points=100, summary_only=summary_only)
    for j, metric in enumerate(METRICS):
        points = [[stamp, i + j + .25 if available else None] for stamp, i, available in window]
        values = [v for _, v in points if v is not None]
        ordered = sorted(values)
        expected = {
            'avg': round(sum(values) / len(values), 3) if values else None,
            'min': min(values) if values else None,
            'max': max(values) if values else None,
            'p95': ordered[math.ceil(len(values) * .95) - 1] if values else None,
            'sample_count': len(values),
            'expected_count': len(window),
            'coverage': round(len(values) / len(window) * 100, 2) if window else 0.0,
        }
        assert result[metric] == {'summary': expected, 'points': [] if summary_only else points}


def test_small_performance_window_does_not_scan_retained_history(monkeypatch):
    ring = NvidiaGpmSampleRing(capacity=14400)
    for i in range(15000):
        ring.append(sampled_at=i + 1, performance={'status': 'available', 'metrics': {METRICS[0]: 40.0}})
    calls = 0
    original = NvidiaGpmSampleRing._time_at

    def timed(self, offset):
        nonlocal calls
        calls += 1
        return original(self, offset)

    monkeypatch.setattr(NvidiaGpmSampleRing, '_time_at', timed)
    result = ring.metric_series_many(METRICS, since=14998, until=15000, max_points=100, summary_only=False)
    assert result[METRICS[0]]['points'] == [[14998, 40.0], [14999, 40.0], [15000, 40.0]]
    assert calls < 64


@pytest.mark.parametrize('sidecar', [False, True])
def test_performance_http_compression_preserves_json_and_identity_clients(tmp_path, sidecar):
    cache = HighresGpuCache()
    for i in range(400):
        cache.performance.append(node_id='node-a', gpu_uuid='gpu-0', gpu_index=0, name='GPU 测试', sampled_at=i + 1,
                                 performance={'profile': 'nvidia.gpm.v1', 'status': 'available',
                                              'metrics': {metric: i % 100 + .25 for metric in METRICS}})
    if sidecar:
        app = create_highres_sidecar_app(HighresSidecarConfig(db_path=tmp_path / 'isolated.db'), cache=cache)
    else:
        app = create_app(cluster_state=ClusterState(local_node_id='preview'), highres_cache=cache)

    @app.get('/uncompressed-test')
    def other_response():
        return {'value': 'sample' * 2000}

    client = TestClient(app)
    params = {'node_id': 'node-a', 'metrics': ','.join(METRICS), 'since': 1, 'until': 400, 'max_points': 1000}
    identity = client.get('/api/highres/performance', params=params, headers={'Accept-Encoding': 'identity'})
    compressed = client.get('/api/highres/performance', params=params, headers={'Accept-Encoding': 'gzip'})
    assert identity.status_code == compressed.status_code == 200
    assert 'content-encoding' not in identity.headers
    assert compressed.headers['content-encoding'] == 'gzip'
    assert 'Accept-Encoding' in compressed.headers['vary']
    assert int(compressed.headers['content-length']) < len(identity.content) / 2
    assert compressed.json() == identity.json() == performance_curves(
        cache, node_id='node-a', metrics=METRICS, since=1, until=400, max_points=1000,
    )
    assert 'content-encoding' not in client.get('/uncompressed-test').headers
    assert not (tmp_path / 'isolated.db').exists()
