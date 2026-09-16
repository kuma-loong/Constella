from __future__ import annotations

import json
import pytest

from constella.cluster import ClusterState, gpu_from_telemetry
from constella.performance import NVIDIA_GPM_METRICS
from constella.schema import AcceleratorPerformance
from constella.telemetry import GPU_METRIC_RULES, PROCESS_METRIC_RULES, telemetry_json

INVALID = [float('nan'), float('inf'), -float('inf'), 1e300, True, 'bad', {}, []]


@pytest.mark.parametrize('field', GPU_METRIC_RULES)
@pytest.mark.parametrize('value', INVALID)
def test_every_scalar_metric_is_isolated(field, value):
    gpu = gpu_from_telemetry('node', {'index': 0, 'uuid': 'gpu', field: value,
                                     'performance': {'profile': 'custom.v1', 'status': 'available',
                                                     'sampled_at': 1, 'metrics': {'healthy': 25}}}, 0)
    assert field in gpu.telemetry_errors
    assert gpu.error is None
    assert gpu.performance.metrics['healthy'] == 25
    json.loads(telemetry_json(gpu.to_dict()))


@pytest.mark.parametrize('metric', [*NVIDIA_GPM_METRICS, 'future.vendor.metric'])
@pytest.mark.parametrize('value', INVALID)
def test_any_performance_metric_name_uses_shared_validation(metric, value):
    p = AcceleratorPerformance(profile='nvidia.gpm.v1', status='available', sampled_at=1,
                               metrics={metric: value, 'healthy.other.metric': 2})
    assert p.invalid_metrics == [metric]
    assert p.metrics == {'healthy.other.metric': 2}


@pytest.mark.parametrize('field,value', [
    ('utilization_gpu', -1), ('utilization_gpu', 101), ('utilization_mem', 101),
    ('memory_used_mb', -1), ('memory_total_mb', 0.5), ('power_watts', -1),
    ('temperature_c', -274), ('clock_sm_mhz', -1),
])
def test_known_scalar_ranges(field, value):
    gpu = gpu_from_telemetry('node', {field: value}, 0)
    assert field in gpu.telemetry_errors


def test_known_metric_ranges_and_unknown_finite_metrics():
    p = AcceleratorPerformance(profile='nvidia.gpm.v1', status='available', sampled_at=1,
        metrics={'nvidia.gpm.sm_active': 101, 'nvidia.gpm.pcie_tx_per_second': -1,
                 'future.signed.metric': -10})
    assert p.metrics == {'future.signed.metric': -10}
    assert len(p.invalid_metrics) == 2


@pytest.mark.parametrize('field', PROCESS_METRIC_RULES)
def test_bad_process_metric_does_not_discard_gpu_or_other_process(field):
    gpu = gpu_from_telemetry('node', {'utilization_gpu': 40, 'processes': [
        {'pid': 1, 'name': 'bad', 'gpu_memory_mb': 10, field: float('nan')},
        {'pid': 2, 'name': 'healthy', 'gpu_memory_mb': 20},
    ]}, 0)
    assert [p.pid for p in gpu.processes] == [2]
    assert gpu.utilization_gpu == 40
    assert gpu.basic_metrics_valid
    assert 'processes' in gpu.telemetry_errors


def test_sidecar_uses_same_validation_without_poisoning_siblings():
    from constella.highres import HighresGpuCache
    cache = HighresGpuCache()
    cache.add_sample_message({'node_id': 'test', 'sampled_at': 100, 'gpus': [
        {'gpu_index': 0, 'uuid': 'bad', 'utilization_gpu': float('inf')},
        {'gpu_index': 1, 'uuid': 'good', 'utilization_gpu': 30},
    ]})
    assert cache.rings[('test', 'bad')].count == 0
    assert cache.rings[('test', 'good')].count == 1
    cache.add_sample_message({'node_id': 'test', 'sampled_at': float('nan'), 'gpus': []})
    assert cache.dropped_samples == 1


def test_healthy_wire_never_invokes_recursive_fallback(monkeypatch):
    def fail(_):
        pytest.fail('healthy frames must not traverse the recursive fallback')
    monkeypatch.setattr('constella.telemetry.json_safe', fail)
    assert json.loads(telemetry_json({'metrics': {'any.metric': 25}, 'history': [1, 2, 3]}))['history'] == [1, 2, 3]


def test_bad_sample_is_visible_and_clears_on_recovery():
    from constella.cluster import AgentHello
    state = ClusterState(local_node_id='test')
    conn = object()
    state.register_hello(AgentHello(node_id='node', hostname='node'), connection_id=conn)
    state.reject_sample('node', connection_id=conn)
    assert state.snapshot().nodes[0].error
    state.ingest_sample({'type': 'sample', 'node_id': 'node', 'seq': 1,
                         'sampled_at': 1, 'snapshot': {'gpus': []}}, connection_id=conn)
    assert state.snapshot().nodes[0].error is None
