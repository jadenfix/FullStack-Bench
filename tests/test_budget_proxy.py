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

    async def slow_failure(r):
        # Held open so all four requests compete for the single call while it is in flight.
        await asyncio.sleep(0.2)
        return httpx.Response(503)

    proxy = BudgetProxy(['private'], transport=httpx.MockTransport(slow_failure))
    s = proxy.register('parallel', 'nvidia/test', tmp_path/'parallel.json', Envelope(calls=1))
    async with client(proxy, s) as c:
        responses = await asyncio.gather(*(c.post('/v1/chat/completions', json=request()) for _ in range(4)))
    assert sorted(r.status_code for r in responses) == [400, 400, 400, 502]
    # Admission was shared (three refused), and the rejected call was refunded afterwards.
    assert s.calls == 0 and s.records[0]['status'] == 'upstream_error' and s.records[0]['refunded']
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


@pytest.mark.asyncio
@pytest.mark.parametrize('options', [{'n': 2}, {'best_of': 4}, {'max_completion_tokens': 99999}, {'n': True}])
async def test_reply_multipliers_and_output_aliases_cannot_bypass_reservations(tmp_path, options):
    def forbidden(req):
        raise AssertionError('unsafe request reached provider')

    proxy = BudgetProxy(['private'], transport=httpx.MockTransport(forbidden))
    s = proxy.register('bounded', 'nvidia/test', tmp_path/'bounded.json')
    async with client(proxy, s) as c:
        assert (await c.post('/v1/chat/completions', json=request(**options))).status_code == 400
    assert s.calls == 0 and s.output_charged == 0
    await proxy.client.aclose()


@pytest.mark.asyncio
async def test_common_tool_calls_survive_but_vendor_controls_are_not_forwarded(tmp_path):
    bodies = []

    def upstream(req):
        bodies.append(json.loads(req.content))
        return httpx.Response(200, json={'usage': {'prompt_tokens': 5, 'completion_tokens': 2}})

    proxy = BudgetProxy(['private'], transport=httpx.MockTransport(upstream))
    s = proxy.register('tools', 'nvidia/test', tmp_path/'tools.json')
    tools = [{'type': 'function', 'function': {'name': 'shell', 'parameters': {'type': 'object'}}}]
    async with client(proxy, s) as c:
        assert (await c.post('/v1/chat/completions', json=request(tools=tools, tool_choice='auto',
            max_completion_tokens=20, n=1, best_of=1, vendor_output_limit=999999))).status_code == 200
    assert bodies[0]['tools'] == tools and bodies[0]['tool_choice'] == 'auto'
    assert bodies[0]['max_tokens'] == 20
    assert not {'max_completion_tokens', 'n', 'best_of', 'vendor_output_limit'} & bodies[0].keys()
    await proxy.client.aclose()


@pytest.mark.asyncio
async def test_provider_rejections_are_refunded_and_passed_through(tmp_path):
    replies = iter([httpx.Response(429, headers={'retry-after': '7'}), httpx.Response(503),
                    httpx.Response(200, json={'usage': {'prompt_tokens': 10, 'completion_tokens': 5},
                                              'choices': [{'message': {'content': 'done'}}]})])
    keys = []

    def upstream(req):
        keys.append(req.headers['authorization'])
        return next(replies)

    proxy = BudgetProxy(['k1', 'k2'], transport=httpx.MockTransport(upstream))
    s = proxy.register('limited', 'nvidia/test', tmp_path/'limited.json', Envelope(calls=1, output_tokens=20))
    async with client(proxy, s) as c:
        limited = await c.post('/v1/chat/completions', json=request())
        assert limited.status_code == 429 and limited.headers['retry-after'] == '7'
        assert (await c.post('/v1/chat/completions', json=request())).status_code == 502
        assert (await c.post('/v1/chat/completions', json=request())).status_code == 200
    receipt = json.loads(s.receipt.read_text())
    assert receipt['calls'] == 1 and receipt['output_charged'] == 5 and receipt['input_charged'] == 10
    assert [r.get('refunded', False) for r in receipt['usage_records']] == [True, True, False]
    assert keys == ['Bearer k1', 'Bearer k2', 'Bearer k1'] and receipt['exhausted'] is None
    await proxy.client.aclose()


@pytest.mark.asyncio
async def test_receipt_keeps_attempts_admissions_refusals_and_usage_apart(tmp_path):
    replies = iter([httpx.Response(429), httpx.Response(503),
                    httpx.Response(200, json={'usage': {'prompt_tokens': 10, 'completion_tokens': 5}, 'choices': []}),
                    httpx.Response(200, json={'choices': []})])
    proxy = BudgetProxy(['k'], transport=httpx.MockTransport(lambda r: next(replies)))
    s = proxy.register('ledger', 'nvidia/test', tmp_path/'ledger.json', Envelope(calls=2, output_tokens=1000))
    async with client(proxy, s) as c:
        statuses = [(await c.post('/v1/chat/completions', json=request())).status_code for _ in range(4)]
        statuses.append((await c.post('/v1/chat/completions', json=request(model='other'))).status_code)
        statuses.append((await c.post('/v1/chat/completions', json=request())).status_code)
        statuses.append((await c.get('/v1/embeddings')).status_code)
    assert statuses == [429, 502, 200, 200, 400, 400, 403]
    receipt = json.loads(s.receipt.read_text())
    assert receipt['accounting'] == {
        'forwarded_attempts': 4, 'admitted_calls': 2,
        'refunded_rejections': {'429': 1, '503': 1},
        'refused_at_admission': {'pin_mismatch': 1, 'budget_exhausted:calls': 1, 'forbidden_endpoint': 1},
        'known_usage_calls': 1, 'unknown_usage_calls': 1,
        'known_prompt_tokens': 10, 'known_completion_tokens': 5,
        'unknown_usage_reserved_input': s.records[3]['input_reservation'], 'unknown_usage_reserved_output': 20,
        'wall_clock_basis': 'first_request',
    }
    # The legacy fields keep their meaning: admitted calls, and exhaustion by limit name.
    assert receipt['calls'] == 2 and receipt['exhausted'] == 'calls'
    assert [r['reason'] for r in receipt['admission_refusals']] == [
        'pin_mismatch', 'budget_exhausted:calls', 'forbidden_endpoint']
    await proxy.client.aclose()
