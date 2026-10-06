import json

import httpx
import pytest

from fsbench.budget_proxy import BudgetProxy, Envelope


def request(model="nvidia/test", **options):
    return {"model": model, "messages": [{"role": "user", "content": "solve"}],
            "max_tokens": 20, **options}


def client(proxy, session):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=proxy), base_url="http://gateway",
                            headers={"Authorization": "Bearer "+session.token})


@pytest.mark.asyncio
async def test_both_tracks_have_independent_equal_caps_and_credentials_stay_outside(tmp_path):
    forwarded = []

    def upstream(req):
        forwarded.append(req)
        return httpx.Response(200, json={"usage": {"prompt_tokens": 10, "completion_tokens": 5},
                                        "choices": [{"message": {"content": "done"}}]})

    proxy = BudgetProxy(["provider-secret"], transport=httpx.MockTransport(upstream))
    for name in ("mini", "rusty"):
        session = proxy.register(name, "nvidia/test", tmp_path / (name+".json"), Envelope(calls=1))
        async with client(proxy, session) as c:
            response = await c.post('/v1/chat/completions', json=request(temperature=0.9, top_p=0.7))
            assert response.status_code == 200
            assert (await c.post('/v1/chat/completions', json=request())).status_code == 400
        receipt = json.loads(session.receipt.read_text())
        assert receipt['calls'] == 1 and receipt['input_charged'] == 10 and receipt['output_charged'] == 5
        assert receipt['exhausted'] == 'calls' and receipt['billed_cost_usd'] is None
        assert session.token not in session.receipt.read_text() and 'provider-secret' not in session.receipt.read_text()
    assert len(forwarded) == 2
    assert all(r.headers['authorization'] == 'Bearer provider-secret' for r in forwarded)
    assert all(json.loads(r.content)['temperature'] == 0.2 and json.loads(r.content)['top_p'] == 1 for r in forwarded)
    await proxy.client.aclose()


@pytest.mark.asyncio
async def test_unknown_usage_retains_reservation_and_cannot_buy_more_output(tmp_path):
    proxy = BudgetProxy(['private'], transport=httpx.MockTransport(lambda r: httpx.Response(200, json={'choices': []})))
    s = proxy.register('unknown', 'nvidia/test', tmp_path/'unknown.json', Envelope(output_tokens=20))
    async with client(proxy, s) as c:
        assert (await c.post('/v1/chat/completions', json=request())).status_code == 200
        assert (await c.post('/v1/chat/completions', json=request())).status_code == 400
    assert s.output_charged == 20 and not s.records[0]['usage_known'] and s.exhausted == 'output_tokens'
    await proxy.client.aclose()


@pytest.mark.asyncio
async def test_wrong_model_route_and_output_cap_never_reach_provider(tmp_path):
    def forbidden(r):
        raise AssertionError('must not forward')

    proxy = BudgetProxy(['private'], transport=httpx.MockTransport(forbidden))
    s = proxy.register('bad', 'nvidia/test', tmp_path/'bad.json')
    async with client(proxy, s) as c:
        assert (await c.post('/v1/chat/completions', json=request(model='other/model'))).status_code == 400
        assert (await c.post('/v1/chat/completions', json=request(max_tokens=99999))).status_code == 400
        assert (await c.post('/v1/embeddings', json=request())).status_code == 403
        assert (await c.get('/v1/models')).json()['data'][0]['id'] == s.model
    assert s.calls == 0
    await proxy.client.aclose()


class Chunks(httpx.AsyncByteStream):
    async def __aiter__(self):
        text = b'data: {"choices":[]}\n\ndata: {"usage":{"prompt_tokens":17,"completion_tokens":8}}\n\ndata: [DONE]\n\n'
        for chunk in (text[:60], text[60:75], text[75:]):
            yield chunk


@pytest.mark.asyncio
async def test_reasoning_policy_is_operator_pinned_and_default_stays_unset(tmp_path):
    bodies = []

    def upstream(req):
        bodies.append(json.loads(req.content))
        return httpx.Response(200, json={'usage': {'prompt_tokens': 10, 'completion_tokens': 1}})

    proxy = BudgetProxy(['private'], transport=httpx.MockTransport(upstream))
    for name, envelope in [('paired', Envelope()), ('review', Envelope(reasoning_effort='low', clear_thinking=True))]:
        s = proxy.register(name, 'nvidia/test', tmp_path/(name+'.json'), envelope)
        async with client(proxy, s) as c:
            assert (await c.post('/v1/chat/completions', json=request(reasoning_effort='max',
                chat_template_kwargs={'clear_thinking': False}, extra_body={'reasoning_budget': 99999}))).status_code == 200
    assert 'reasoning_effort' not in bodies[0] and 'chat_template_kwargs' not in bodies[0]
    assert bodies[1]['reasoning_effort'] == 'low' and bodies[1]['chat_template_kwargs'] == {'clear_thinking': True}
    assert all('extra_body' not in b and 'reasoning_budget' not in b for b in bodies)
    await proxy.client.aclose()


@pytest.mark.asyncio
async def test_stream_usage_across_chunk_boundaries_is_metered(tmp_path):
    proxy = BudgetProxy(['private'], transport=httpx.MockTransport(lambda r: httpx.Response(200, stream=Chunks())))
    s = proxy.register('stream', 'nvidia/test', tmp_path/'stream.json')
    async with client(proxy, s) as c:
        response = await c.post('/v1/chat/completions', json=request(stream=True))
    assert '[DONE]' in response.text
    assert s.input_charged == 17 and s.output_charged == 8 and s.records[0]['usage_known']
    await proxy.client.aclose()


@pytest.mark.asyncio
async def test_parallel_requests_share_reservations_and_upstream_failures_are_recorded(tmp_path):
    import asyncio

    proxy = BudgetProxy(['private'], transport=httpx.MockTransport(lambda r: httpx.Response(503)))
    s = proxy.register('parallel', 'nvidia/test', tmp_path/'parallel.json', Envelope(calls=1))
    async with client(proxy, s) as c:
        responses = await asyncio.gather(*(c.post('/v1/chat/completions', json=request()) for _ in range(4)))
    assert sorted(r.status_code for r in responses) == [400, 400, 400, 502]
    assert s.calls == 1 and s.records[0]['status'] == 'upstream_error'
    await proxy.client.aclose()


@pytest.mark.asyncio
async def test_trial_wall_cutoff_is_distinct_from_provider_failure(tmp_path):
    import asyncio
    import time

    async def slow(req):
        await asyncio.sleep(.1)
        return httpx.Response(200, json={'usage': {'prompt_tokens': 1, 'completion_tokens': 1}})

    proxy = BudgetProxy(['private'], transport=httpx.MockTransport(slow))
    s = proxy.register('wall', 'nvidia/test', tmp_path/'wall.json', Envelope(wall_seconds=1, request_seconds=10))
    s.started = time.monotonic()-.98
    async with client(proxy, s) as c:
        response = await c.post('/v1/chat/completions', json=request())
    assert response.status_code == 400 and response.json()['error']['code'] == 'budget_exhausted'
    assert s.exhausted == 'wall' and s.records[0]['status'] == 'trial_wall_exhausted'
    assert not s.records[0]['usage_known'] and s.output_charged == 20
    await proxy.client.aclose()
