"""Exercise Live event ordering without a network connection or real devices."""
import asyncio
import json
import os
import unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch

from live_demo import server as s
from live_demo.live_tools import build_live_config


def event(**fields):
    content = dict(input_transcription=None, output_transcription=None,
                   model_turn=None, interrupted=False, turn_complete=False)
    content.update(fields)
    return NS(server_content=NS(**content), tool_call=None, tool_call_cancellation=None)


def words(text, finished=False):
    return NS(text=text, finished=finished)


class LiveTurnTests(unittest.IsolatedAsyncioTestCase):
    async def run_events(self, events, initial=None):
        session = s.IntakeSession('live-turn-test', owner='owner')
        s.sessions[session.session_id] = session
        self.addCleanup(s.sessions.pop, session.session_id, None)
        if initial:
            s.append_turn(session, 'Agent', initial)
        sent, context = [], []
        delivered = asyncio.Event()

        class WS:
            headers = {'host': '127.0.0.1:4177', 'origin': 'http://127.0.0.1:4177'}
            client = NS(host='127.0.0.1')
            url = NS(scheme='ws')
            cookies = {'intake_owner': 'owner'}
            query_params = {'session_id': session.session_id}
            async def accept(self): pass
            async def close(self, **kwargs): pass
            async def send_json(self, payload): sent.append(payload)
            async def receive_text(self):
                await delivered.wait()
                return json.dumps({'type': 'close'})

        class Live:
            async def send_client_content(self, **kwargs): context.append(kwargs)
            async def receive(self):
                for item in events:
                    yield item
                delivered.set()
                await asyncio.Event().wait()

        class Connection:
            async def __aenter__(self): return Live()
            async def __aexit__(self, *args): pass

        configurations = []
        def connect(**kwargs):
            configurations.append(kwargs['config'])
            return Connection()
        fake = NS(aio=NS(live=NS(connect=connect)))
        with patch.object(s, '_has_api_key', return_value=True), \
             patch.object(s, '_live_client', return_value=fake), \
             patch.object(s, '_run_workflow_cached', new=AsyncMock(return_value=s.build_initial_workflow_state())):
            await asyncio.wait_for(s.live_voice(WS()), 2)
        seeded = bool(initial) and initial != s.GREETING
        self.assertEqual(bool(configurations[0].history_config), seeded)
        if seeded:
            self.assertTrue(configurations[0].history_config.initial_history_in_client_content)
        return session, sent, context

    async def test_finished_agent_segments_do_not_run_together(self):
        session, sent, _ = await self.run_events([
            event(output_transcription=words('Hello. ', True)),
            event(output_transcription=words('I will wait.', True)),
            event(turn_complete=True),
        ])
        self.assertEqual([t['text'] for t in session.transcript], ['Hello.', 'I will wait.'])
        finals = [m for m in sent if m['type'] == 'transcript' and m['final']]
        self.assertEqual(len(finals), 2)
        self.assertNotEqual(finals[0]['id'], finals[1]['id'])

    async def test_interrupted_payload_cannot_requeue_cancelled_speech(self):
        session, sent, _ = await self.run_events([
            event(output_transcription=words('Let me explain')),
            event(interrupted=True, input_transcription=words('Stop.', True),
                  output_transcription=words(' cancelled continuation'),
                  model_turn=NS(parts=[NS(inline_data=NS(data=b'cancelled', mime_type='video/mp4'))])),
            event(output_transcription=words('Okay, I will wait.', True), turn_complete=True),
        ])
        self.assertEqual([t['text'] for t in session.transcript],
                         ['Let me explain', 'Stop.', 'Okay, I will wait.'])
        self.assertFalse(any(m['type'] == 'avatar_video' for m in sent))
        stopped = next(i for i, m in enumerate(sent) if m['type'] == 'interrupted')
        final = next(i for i, m in enumerate(sent) if m['type'] == 'transcript' and m['final'])
        self.assertLess(stopped, final)

    async def test_initial_visible_greeting_is_restored_without_triggering_speech(self):
        greeting = 'What can I help you with today?'
        _, _, context = await self.run_events([event(turn_complete=True)], initial=greeting)
        self.assertEqual(len(context), 1)
        self.assertTrue(context[0]['turn_complete'])
        self.assertEqual(context[0]['turns'][0].role, 'model')
        self.assertEqual(context[0]['turns'][0].parts[0].text, greeting)

    async def test_new_call_speaks_the_greeting_once(self):
        session, sent, context = await self.run_events([
            event(output_transcription=words('Hi, I can help with changes to your mortgage.', True), turn_complete=True),
        ], initial=s.GREETING)
        self.assertEqual(len(context), 1)
        self.assertEqual(context[0]['turns'].role, 'user')
        self.assertIn(s.GREETING, context[0]['turns'].parts[0].text)
        self.assertTrue(context[0]['turn_complete'])
        self.assertTrue(session.greeted)
        # The spoken greeting reuses the visible greeting turn rather than adding a second one.
        self.assertEqual([t['text'] for t in session.transcript], [s.GREETING])
        greeting_id = session.transcript[0]['id']
        self.assertTrue(all(m['id'] == greeting_id for m in sent if m['type'] == 'transcript'))

    def test_voice_and_avatar_allow_speech_to_interrupt(self):
        for name in ['', 'Kira']:
            config = build_live_config(avatar_name=name)
            self.assertEqual(config.realtime_input_config.activity_handling.value,
                             'START_OF_ACTIVITY_INTERRUPTS')

    def test_name_hints_apply_to_input_only_in_voice_and_avatar_calls(self):
        with patch.dict(os.environ, {'MORTGAGE_TRANSCRIPTION_VOCABULARY': ' Maya Chen, Chen, ,Chen '}):
            for name in ['', 'Kira']:
                config = build_live_config(avatar_name=name)
                self.assertEqual(config.input_audio_transcription.custom_vocabulary,
                                 ['Maya Chen', 'Chen'])
                self.assertIsNone(config.output_audio_transcription.custom_vocabulary)
                self.assertNotIn('Maya Chen', config.system_instruction)

    async def test_idle_avatar_frames_do_not_split_spoken_caller_turn(self):
        video = NS(parts=[NS(inline_data=NS(data=b'idle frame', mime_type='video/mp4'))])
        session, sent, _ = await self.run_events([
            event(input_transcription=words('My name is ')),
            event(model_turn=video),
            event(input_transcription=words('Maya Chen.', True)),
        ])
        self.assertEqual([t['text'] for t in session.transcript], ['My name is Maya Chen.'])
        self.assertEqual(len([m for m in sent if m['type'] == 'avatar_video']), 1)

    def test_no_name_is_assumed_without_vocabulary(self):
        with patch.dict(os.environ, {'MORTGAGE_TRANSCRIPTION_VOCABULARY': ''}):
            self.assertIsNone(build_live_config().input_audio_transcription.custom_vocabulary)


class SyncLatencyTests(unittest.IsolatedAsyncioTestCase):
    async def test_sync_replies_before_slow_review_finishes(self):
        """Giving a phone number must not leave Kira waiting on the background review."""
        session = s.IntakeSession('sync-latency', owner='owner')
        s.sessions[session.session_id] = session
        self.addCleanup(s.sessions.pop, session.session_id, None)
        review_started, release_review, replied = asyncio.Event(), asyncio.Event(), asyncio.Event()
        responses = []

        async def slow_review(_session):
            review_started.set()
            await release_review.wait()
            return s.build_initial_workflow_state()

        call = NS(id='call-1', name='sync_service_request',
                  args={'contact_method': '905-555-0162', 'request_summary': 'Make a prepayment.'})
        tool_event = NS(server_content=None, tool_call=NS(function_calls=[call]), tool_call_cancellation=None)

        class WS:
            headers = {'host': '127.0.0.1:4177', 'origin': 'http://127.0.0.1:4177'}
            client = NS(host='127.0.0.1'); url = NS(scheme='ws')
            cookies = {'intake_owner': 'owner'}; query_params = {'session_id': session.session_id}
            async def accept(self): pass
            async def close(self, **kwargs): pass
            async def send_json(self, payload): pass
            async def receive_text(self):
                await replied.wait()
                release_review.set()
                return json.dumps({'type': 'close'})

        class Live:
            async def send_client_content(self, **kwargs): pass
            async def send_tool_response(self, function_responses):
                # The reply must arrive while the review is still running.
                responses.append((review_started.is_set(), release_review.is_set(), function_responses[0].response))
                replied.set()
            async def receive(self):
                yield tool_event
                await asyncio.Event().wait()

        class Connection:
            async def __aenter__(self): return Live()
            async def __aexit__(self, *args): pass

        fake = NS(aio=NS(live=NS(connect=lambda **kw: Connection())))
        with patch.object(s, '_has_api_key', return_value=True), \
             patch.object(s, '_live_client', return_value=fake), \
             patch.object(s, '_run_workflow_cached', side_effect=slow_review):
            await asyncio.wait_for(s.live_voice(WS()), 2)

        self.assertEqual(len(responses), 1)
        _, review_finished_before_reply, result = responses[0]
        self.assertFalse(review_finished_before_reply)
        self.assertNotIn('contact_method', result['open_items'])
        self.assertEqual(session.agent_notes['contact_method'], '905-555-0162')

    async def test_urgent_route_is_announced_once(self):
        sent = []
        session = s.IntakeSession('announce', live_session=NS(send_client_content=AsyncMock(side_effect=lambda **kw: sent.append(kw))))
        workflow = s.build_initial_workflow_state()
        workflow['security_hardship_gate']['final_routing_decision'] = 'security_review'
        await s.announce_urgent_route(session, workflow)
        await s.announce_urgent_route(session, workflow)
        self.assertEqual(len(sent), 1)
        self.assertIn('security review', sent[0]['turns'].parts[0].text)
        routine = s.build_initial_workflow_state()
        await s.announce_urgent_route(s.IntakeSession('quiet', live_session=NS(send_client_content=AsyncMock())), routine)
