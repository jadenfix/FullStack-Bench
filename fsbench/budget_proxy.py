"""Operator-owned NVIDIA gateway with trial-scoped call and token limits.

Only the gateway holds upstream credentials. Solvers receive a trial token. All forwarded
attempts, including compaction and retries, consume the same envelope. Unknown usage keeps
its conservative reservation; invoices are never inferred from token counters.

Accounting contract (the receipt's `accounting` block; the gateway, not a harness's own
counter, is the episode's budget record):
- `forwarded_attempts`: requests sent upstream, whatever happened to them.
- `admitted_calls`: forwarded attempts still charged to the envelope (`calls`). A provider
  rejection (any non-200 status before generation) is refunded and not admitted.
- `refunded_rejections`: those refunds, by HTTP status.
- `refused_at_admission`: requests the gateway answered itself without forwarding, by
  reason (`budget_exhausted:<limit>`, `pin_mismatch`, `forbidden_endpoint`,
  `body_too_large`). They cost nothing and are not attempts.
- `known_usage_calls` / `unknown_usage_calls`: admitted calls with and without
  provider-reported usage. Unknown usage stays charged at its reservation, shown in
  `unknown_usage_reserved_input` / `_output`; it is a bound, not a measurement.
- `known_prompt_tokens` / `known_completion_tokens`: provider-reported totals.
- `wall_clock_basis`: the wall limit runs from the first request, not from registration,
  so environment build and setup time are not charged to the solver.
- `billed_cost_usd` stays null: no invoice is inferred.
"""

import asyncio
import hashlib
import json
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx

UPSTREAM = "https://integrate.api.nvidia.com/v1/chat/completions"


@dataclass
class Envelope:
    calls: int = 250
    input_tokens: int = 12_000_000
    output_tokens: int = 500_000
    max_reply: int = 16_384
    wall_seconds: int = 18_000
    request_seconds: int = 600
    temperature: float = 0.2
    top_p: float = 1.0
    reasoning_effort: str | None = None
    clear_thinking: bool | None = None

    def __post_init__(self):
        if any(not isinstance(v, int) or isinstance(v, bool) or v <= 0 for v in
               (self.calls, self.input_tokens, self.output_tokens, self.max_reply,
                self.wall_seconds, self.request_seconds)):
            raise ValueError("envelope limits must be positive integers")
        if self.reasoning_effort not in (None, 'low', 'high', 'max'):
            raise ValueError("unsupported pinned reasoning effort")
        if self.clear_thinking is not None and not isinstance(self.clear_thinking, bool):
            raise ValueError("clear_thinking must be a boolean")


@dataclass
class Session:
    name: str
    model: str
    receipt: Path
    envelope: Envelope
    token: str = field(default_factory=lambda: secrets.token_hex(32), repr=False)
    started: float | None = None
    calls: int = 0
    input_charged: int = 0
    output_charged: int = 0
    records: list = field(default_factory=list)
    refusals: list = field(default_factory=list)
    exhausted: str | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)

    def save(self):
        data = {"session": self.name, "model": self.model, "envelope": vars(self.envelope),
                "calls": self.calls, "input_charged": self.input_charged,
                "output_charged": self.output_charged, "exhausted": self.exhausted,
                "usage_records": self.records, "admission_refusals": self.refusals,
                "accounting": self.accounting(), "billed_cost_usd": None,
                "cost_basis": "NVIDIA catalog prototype endpoint; advertised free; no invoice received"}
        self.receipt.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.receipt.with_suffix('.pending')
        temporary.write_text(json.dumps(data, indent=2) + '\n')
        temporary.replace(self.receipt)


    def refuse(self, reason: str) -> None:
        """Record a request answered by the gateway itself, without forwarding it."""
        since = None if self.started is None else round(time.monotonic() - self.started, 3)
        self.refusals.append({'reason': reason, 'seconds_since_first_request': since})
        self.save()

    def accounting(self) -> dict:
        """The receipt's quantities kept apart; see the module docstring."""
        refunded: dict[str, int] = {}
        for r in self.records:
            if r.get('refunded'):
                key = str(r.get('http_status'))
                refunded[key] = refunded.get(key, 0) + 1
        refused: dict[str, int] = {}
        for r in self.refusals:
            refused[r['reason']] = refused.get(r['reason'], 0) + 1
        admitted = [r for r in self.records if not r.get('refunded')]
        known = [r for r in admitted if r.get('usage_known')]
        unknown = [r for r in admitted if not r.get('usage_known')]
        return {
            'forwarded_attempts': len(self.records),
            'admitted_calls': len(admitted),
            'refunded_rejections': refunded,
            'refused_at_admission': refused,
            'known_usage_calls': len(known),
            'unknown_usage_calls': len(unknown),
            'known_prompt_tokens': sum(r.get('prompt_tokens', 0) for r in known),
            'known_completion_tokens': sum(r.get('completion_tokens', 0) for r in known),
            'unknown_usage_reserved_input': sum(r['input_reservation'] for r in unknown),
            'unknown_usage_reserved_output': sum(r['output_reservation'] for r in unknown),
            'wall_clock_basis': 'first_request',
        }


class BudgetProxy:
    def __init__(self, keys: list[str], *, transport=None):
        if not keys:
            raise ValueError("upstream keys required")
        self._keys = keys
        self.sessions: dict[str, Session] = {}
        self.client = httpx.AsyncClient(transport=transport, follow_redirects=False)

    def register(self, name: str, model: str, receipt: Path, envelope: Envelope | None = None) -> Session:
        if any(s.name == name for s in self.sessions.values()):
            raise ValueError("session names cannot be reused")
        s = Session(name, model, receipt, envelope or Envelope())
        self.sessions[s.token] = s
        s.save()
        return s

    async def __call__(self, scope, receive, send):
        if scope['type'] == 'lifespan':
            while True:
                event = await receive()
                if event['type'] == 'lifespan.startup':
                    await send({'type': 'lifespan.startup.complete'})
                elif event['type'] == 'lifespan.shutdown':
                    await self.client.aclose()
                    await send({'type': 'lifespan.shutdown.complete'})
                    return
        if scope['type'] != 'http':
            return
        headers = dict(scope.get('headers', []))
        auth = headers.get(b'authorization', b'').decode(errors='replace')
        session = self.sessions.get(auth.removeprefix('Bearer ')) if auth.startswith('Bearer ') else None
        if session is None:
            return await respond(send, 401, 'unknown trial token')
        if scope['method'] == 'GET' and scope['path'] == '/v1/models':
            return await respond(send, 200, {'data': [{'id': session.model, 'object': 'model'}]})
        if scope['method'] != 'POST' or scope['path'] != '/v1/chat/completions' or scope.get('query_string'):
            session.refuse('forbidden_endpoint')
            return await respond(send, 403, 'only the pinned chat endpoint is allowed')
        raw = bytearray()
        while True:
            event = await receive()
            if event['type'] == 'http.disconnect':
                return
            raw.extend(event.get('body', b''))
            if len(raw) > 8_000_000:
                session.refuse('body_too_large')
                return await respond(send, 413, 'request body exceeds envelope')
            if not event.get('more_body'):
                break
        try:
            body = json.loads(raw)
            if body['model'] != session.model or not isinstance(body['messages'], list):
                raise ValueError()
            maximum = body.get('max_tokens', body.get('max_completion_tokens', session.envelope.max_reply))
            alternate = body.get('max_completion_tokens', maximum)
            if alternate != maximum or isinstance(alternate, bool) or not isinstance(alternate, int):
                raise ValueError()
            for option in ('n', 'best_of'):
                count = body.get(option, 1)
                if isinstance(count, bool) or not isinstance(count, int) or count != 1:
                    raise ValueError()
            if isinstance(maximum, bool) or not isinstance(maximum, int) or not 0 < maximum <= session.envelope.max_reply:
                raise ValueError()
        except (ValueError, TypeError, KeyError):
            session.refuse('pin_mismatch')
            return await respond(send, 400, 'model, messages or output cap differ from the pin')
        # Forward only the common chat contract. Aliases or vendor controls cannot
        # multiply completions or override the one reply reservation downstream.
        body = {key: value for key, value in body.items() if key in {
            'model', 'messages', 'stream', 'tools', 'tool_choice', 'parallel_tool_calls',
            'response_format', 'stop',
        }}
        body['max_tokens'] = maximum
        body['temperature'] = session.envelope.temperature
        body['top_p'] = session.envelope.top_p
        # Only operator pins may change the provider's reasoning policy. The paired
        # Super tracks leave these unset; a cross-family reviewer can require them.
        for key in ('reasoning_effort', 'reasoning_budget', 'chat_template_kwargs', 'extra_body'):
            body.pop(key, None)
        if session.envelope.reasoning_effort is not None:
            body['reasoning_effort'] = session.envelope.reasoning_effort
        if session.envelope.clear_thinking is not None:
            body['chat_template_kwargs'] = {'clear_thinking': session.envelope.clear_thinking}
        if body.get('stream'):
            body['stream_options'] = {'include_usage': True}
        payload = json.dumps(body).encode()
        reservation = len(payload) + 64 * len(body['messages']) + 4096
        async with session.lock:
            e = session.envelope
            if session.started is None:
                session.started = time.monotonic()
            reason = ('wall' if time.monotonic()-session.started >= e.wall_seconds else
                      'calls' if session.calls >= e.calls else
                      'input_tokens' if session.input_charged+reservation > e.input_tokens else
                      'output_tokens' if session.output_charged+maximum > e.output_tokens else None)
            if reason:
                session.exhausted = reason
                session.refuse('budget_exhausted:'+reason)
                return await respond(send, 400, 'trial budget exhausted: '+reason, code='budget_exhausted')
            session.calls += 1
            session.input_charged += reservation
            session.output_charged += maximum
            # Attempts, not admitted calls, rotate keys: a refunded rejection still moves on.
            record = {'attempt': len(session.records)+1, 'request_sha256': hashlib.sha256(payload).hexdigest(),
                      'input_reservation': reservation, 'output_reservation': maximum,
                      'usage_known': False, 'status': 'in_flight'}
            session.records.append(record)
            session.save()
        started = time.monotonic()
        usage = None
        response = None
        try:
            async with asyncio.timeout(min(e.request_seconds, max(0.01, e.wall_seconds-(started-session.started)))):
                request = self.client.build_request('POST', UPSTREAM, content=payload,
                    headers={'authorization': 'Bearer '+self._keys[(record['attempt']-1) % len(self._keys)],
                             'content-type': 'application/json'}, timeout=httpx.Timeout(e.request_seconds, connect=20))
                response = await self.client.send(request, stream=True)
                record['http_status'] = response.status_code
                if response.status_code != 200:
                    # The provider refused before generating anything, so neither the call nor its
                    # tokens were used. Keeping them would score a rate limit as solver exhaustion.
                    record['status'] = 'upstream_error'
                    async with session.lock:
                        session.calls -= 1
                        session.input_charged -= reservation
                        session.output_charged -= maximum
                        record['refunded'] = True
                    if response.status_code == 429:
                        retry = response.headers.get('retry-after')
                        return await respond(send, 429, 'upstream rate limited; retry later', code='rate_limited',
                                             headers=[(b'retry-after', retry.encode())] if retry else [])
                    return await respond(send, 502, 'upstream returned HTTP '+str(response.status_code))
                await send({'type': 'http.response.start', 'status': 200,
                            'headers': [(b'content-type', response.headers.get('content-type', 'application/json').encode())]})
                pending = b''
                async for chunk in response.aiter_bytes():
                    pending += chunk
                    if body.get('stream'):
                        lines = pending.split(b'\n')
                        pending = lines.pop()
                        for line in lines:
                            if line.startswith(b'data:'):
                                try:
                                    item = json.loads(line[5:])
                                    if item.get('usage'):
                                        usage = item['usage']
                                except (ValueError, AttributeError):
                                    pass
                    await send({'type': 'http.response.body', 'body': chunk, 'more_body': True})
                if not body.get('stream'):
                    usage = json.loads(pending).get('usage')
                elif pending.startswith(b'data:'):
                    try:
                        usage = json.loads(pending[5:]).get('usage') or usage
                    except (ValueError, AttributeError):
                        pass
                await send({'type': 'http.response.body', 'body': b''})
                record['status'] = 'completed'
        except (httpx.HTTPError, TimeoutError, OSError, ValueError) as error:
            record['status'] = 'upstream_or_stream_error'
            record['error_type'] = type(error).__name__
            wall_exhausted = isinstance(error, TimeoutError) and time.monotonic()-session.started >= e.wall_seconds-0.01
            if wall_exhausted:
                record['status'] = 'trial_wall_exhausted'
                session.exhausted = 'wall'
            if response is None:
                if wall_exhausted:
                    await respond(send, 400, 'trial budget exhausted: wall', code='budget_exhausted')
                else:
                    await respond(send, 502, 'upstream request failed')
            else:
                raise
        finally:
            if response is not None:
                await response.aclose()
            async with session.lock:
                valid_usage = isinstance(usage, dict) and all(isinstance(usage.get(k), int) and
                    not isinstance(usage[k], bool) and usage[k] >= (1 if k == 'prompt_tokens' else 0)
                    for k in ('prompt_tokens', 'completion_tokens'))
                if valid_usage:
                    p, c = usage['prompt_tokens'], usage['completion_tokens']
                    if p <= reservation and c <= maximum:
                        session.input_charged += p-reservation
                        session.output_charged += c-maximum
                        record.update(usage_known=True, prompt_tokens=p, completion_tokens=c)
                    else:
                        record['status'] = 'usage_exceeded_reservation'
                        session.exhausted = 'accounting_anomaly'
                record['seconds'] = round(time.monotonic()-started, 3)
                session.save()


async def respond(send, status, message, *, code='gateway_error', headers=()):
    body = message if isinstance(message, dict) else {'error': {'message': message, 'type': code, 'code': code}}
    await send({'type': 'http.response.start', 'status': status,
                'headers': [(b'content-type', b'application/json'), *headers]})
    await send({'type': 'http.response.body', 'body': json.dumps(body).encode()})
