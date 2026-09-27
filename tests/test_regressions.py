"""Deterministic regressions. All model responses here are controlled fixtures."""
import asyncio, base64, io, json, sys, time, unittest, zipfile
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import servicing_rules as r
from agent import blank_request, build_initial_workflow_state
from schemas import ServiceRequest
from mortgage_directory import MORTGAGE_RECORDS, lookup_mortgage
from payment_math import amortize, build_scenario, level_payment
from live_demo import server as s
from fastapi.testclient import TestClient

FUTURE = (date.today() + timedelta(days=30)).isoformat()


def request(**updates):
    c = blank_request()
    c.update(borrower_name='Maya Singh', mortgage_number='MTG-40117', property_postal_code='M4C 1B5', caller_role='borrower',
             contact_method='416-555-0134', request_summary='Switch to accelerated bi-weekly payments.', raw_summary='Frequency change.')
    changes = updates.pop('changes', {})
    c.update(updates)
    c['requested_changes'] = {**c['requested_changes'], 'new_payment_frequency': 'accelerated_bi_weekly', 'effective_date': FUTURE, **changes}
    return c


def workflow(c, kind='payment_change', secondary=()):
    cls = dict(request_type=kind, secondary_request_types=list(secondary), priority='medium', priority_rationale='Test fixture', customer_needs=[])
    v = r.validate_required_fields(c, cls)
    dec = r.apply_servicing_rules(c, v, cls)
    docs = r.generate_document_checklist(c, cls, dec)
    gate = r.security_and_hardship_gate(c, v, cls, dec)
    packet = r.build_service_request_packet(c, v, cls, dec, docs, gate)
    return dict(normalized_request=c, request_classification=cls, field_validation=v, servicing_decision=dec,
                document_checklist=docs, security_hardship_gate=gate, service_request_packet=packet)


def route(w):
    return w['security_hardship_gate']['final_routing_decision']


def rule_ids(w):
    return [f['rule_id'] for f in w['servicing_decision']['findings']] + [x['signal_id'] for x in w['security_hardship_gate']['signals']]


class PaymentMathTests(unittest.TestCase):
    def test_canadian_fixed_payment_matches_known_value(self):
        # $400k, 5%, 25 years, semi-annual compounding: $2,326.42 monthly.
        self.assertAlmostEqual(level_payment(400000, 0.05, 25, 12, 'semi_annual'), 2326.42, places=2)

    def test_accelerated_bi_weekly_pays_off_sooner(self):
        monthly = level_payment(400000, 0.05, 25, 12, 'semi_annual')
        base = amortize(400000, 0.05, monthly, 12, 'semi_annual')
        fast = amortize(400000, 0.05, monthly / 2, 26, 'semi_annual')
        self.assertAlmostEqual(base['years'], 25, places=1)
        self.assertAlmostEqual(fast['years'], 21.5, places=1)
        self.assertLess(fast['total_interest'], base['total_interest'] - 45000)

    def test_payment_below_interest_never_pays_off(self):
        self.assertFalse(amortize(400000, 0.05, 100, 12, 'semi_annual')['pays_off'])

    def test_scenario_prepayment_reduces_balance_and_interest(self):
        x = build_scenario(MORTGAGE_RECORDS['MTG40117'], prepayment_amount=20000)
        self.assertEqual(x['proposed']['yearly_balances'][0], MORTGAGE_RECORDS['MTG40117']['balance'] - 20000)
        self.assertGreater(x['interest_saved'], 0)
        self.assertAlmostEqual(x['current']['years'], 22, places=1)

    def test_prepayment_above_balance_is_rejected(self):
        with self.assertRaises(ValueError):
            build_scenario(MORTGAGE_RECORDS['MTG40117'], prepayment_amount=10_000_000)


class DirectoryTests(unittest.TestCase):
    def test_details_only_after_verification(self):
        for name, postal in [('', ''), ('Maya Singh', ''), ('Maya Singh', 'K1N 6N5'), ('Someone Else', 'M4C 1B5')]:
            with self.subTest(name=name, postal=postal):
                result = lookup_mortgage('MTG-40117', name, postal)
                self.assertFalse(result['verified'])
                self.assertNotIn('balance', result)
                self.assertNotIn('borrowers', result)
        verified = lookup_mortgage('mtg 4O117', 'maya singh', 'm4c1b5')
        self.assertTrue(verified['verified'])
        self.assertIn('balance', verified)
        self.assertNotIn('property_postal_code', verified)

    def test_co_borrower_can_verify(self):
        self.assertTrue(lookup_mortgage('MTG-40117', 'Arjun Singh', 'M4C 1B5')['verified'])

    def test_unknown_number(self):
        self.assertFalse(lookup_mortgage('MTG-00000', 'Maya Singh', 'M4C 1B5')['found'])


class RuleTests(unittest.TestCase):
    def test_verified_frequency_change_is_ready(self):
        w = workflow(request())
        self.assertEqual(route(w), 'ready_to_process')
        self.assertTrue(w['field_validation']['identity_verified'])

    def test_payment_change_needs_detail_and_start_date(self):
        w = workflow(request(changes=dict(new_payment_frequency='not specified')))
        self.assertIn('payment_change_detail', w['field_validation']['missing_fields'])
        w = workflow(request(changes=dict(effective_date='not specified')))
        self.assertIn('effective_date', w['field_validation']['missing_fields'])

    def test_past_and_invalid_dates_block(self):
        for value in ['banana', '2026-02-30', (date.today() - timedelta(days=3)).isoformat()]:
            with self.subTest(value=value):
                self.assertEqual(r.validate_required_fields(request(changes=dict(effective_date=value)), dict(request_type='payment_change', priority='low', priority_rationale='x'))['intake_status'], 'missing_info')

    def test_prepayment_within_allowance_has_no_charge(self):
        w = workflow(request(changes=dict(new_payment_frequency='not specified', prepayment_amount_cad=25000)), 'prepayment')
        self.assertEqual(route(w), 'ready_to_process')
        self.assertTrue(any('No prepayment charge' in n for n in w['servicing_decision']['servicing_notes']))

    def test_prepayment_over_allowance_goes_to_specialist(self):
        c = request(borrower_name='Priya Shah', mortgage_number='MTG-61845', property_postal_code='V5K 0A1',
                    changes=dict(new_payment_frequency='not specified', prepayment_amount_cad=20000))
        w = workflow(c, 'prepayment')
        self.assertEqual(route(w), 'specialist_review')
        self.assertIn('PRE-001', rule_ids(w))
        self.assertTrue(any("three months' interest" in n for n in w['servicing_decision']['servicing_notes']))

    def test_payment_increase_allowance(self):
        current = MORTGAGE_RECORDS['MTG40117']['payment_amount']
        ok = workflow(request(changes=dict(new_payment_frequency='not specified', new_payment_amount_cad=round(current * 1.1, 2))))
        self.assertEqual(route(ok), 'ready_to_process')
        high = workflow(request(changes=dict(new_payment_frequency='not specified', new_payment_amount_cad=round(current * 1.3, 2))))
        self.assertIn('PAY-001', rule_ids(high))
        low = workflow(request(changes=dict(new_payment_frequency='not specified', new_payment_amount_cad=1000)))
        self.assertIn('PAY-002', rule_ids(low))

    def test_payout_for_sale_needs_signed_request_and_agreement(self):
        c = request(borrower_name='Chris Park', mortgage_number='MTG-93006', property_postal_code='H2X 1Y4',
                    changes=dict(new_payment_frequency='not specified', payout_date=FUTURE, payout_reason='sale'))
        w = workflow(c, 'payout_discharge')
        items = [i['item'] for i in w['document_checklist']['items']]
        self.assertIn('Payout statement request signed by all borrowers', items)
        self.assertIn('Agreement of purchase and sale', items)
        self.assertEqual(route(w), 'needs_documents')

    def test_captured_document_satisfies_only_its_own_type(self):
        c = request(borrower_name='Chris Park', mortgage_number='MTG-93006', property_postal_code='H2X 1Y4',
                    changes=dict(new_payment_frequency='not specified', payout_date=FUTURE, payout_reason='sale'))
        c = r.prepare_request(c, [dict(id='cap1', document_types=['purchase_agreement'])])
        items = workflow(c, 'payout_discharge')['document_checklist']['items']
        self.assertEqual([i['item'] for i in items if i['already_provided']], ['Agreement of purchase and sale'])

    def test_model_cannot_mint_received_documents(self):
        c = r.prepare_request(request(evidence_records=[dict(document_type='void_cheque', status='received', evidence_ids=['invented'])]))
        self.assertEqual(c['evidence_records'][0]['status'], 'available')
        self.assertEqual(c['evidence_records'][0]['evidence_ids'], [])

    def test_identity_mismatch_goes_to_security(self):
        for kwargs in [dict(borrower_name='Someone Else'), dict(property_postal_code='K1N 6N5')]:
            with self.subTest(kwargs=kwargs):
                w = workflow(request(**kwargs))
                self.assertEqual(route(w), 'security_review')
                self.assertFalse(w['field_validation']['identity_verified'])

    def test_unknown_mortgage_asks_again(self):
        w = workflow(request(mortgage_number='MTG-00000'))
        self.assertEqual(route(w), 'needs_documents')

    def test_third_party_caller_goes_to_security(self):
        for role in ['other_third_party', 'authorized_third_party']:
            with self.subTest(role=role):
                self.assertEqual(route(workflow(request(caller_role=role))), 'security_review')

    def test_bank_change_with_money_movement_goes_to_security(self):
        w = workflow(request(changes=dict(new_bank_account=True, prepayment_amount_cad=5000)), 'payment_change', ['prepayment'])
        self.assertIn('SEC-003', rule_ids(w))
        self.assertEqual(route(w), 'security_review')

    def test_recent_contact_change_with_money_movement(self):
        c = request(borrower_name='Alex Chen', mortgage_number='MTG-88410', property_postal_code='K1N 6N5',
                    changes=dict(new_payment_frequency='not specified', prepayment_amount_cad=10000))
        self.assertIn('SEC-004', rule_ids(workflow(c, 'prepayment')))
        plain = request(borrower_name='Alex Chen', mortgage_number='MTG-88410', property_postal_code='K1N 6N5',
                        changes=dict(new_payment_frequency='monthly'))
        self.assertNotIn('SEC-004', rule_ids(workflow(plain)))

    def test_suspicious_message_goes_to_security(self):
        c = request(circumstances=[dict(category='suspicious_message', status='present', description='Email asked to pay a new account')])
        self.assertEqual(route(workflow(c)), 'security_review')

    def test_hardship_and_arrears_go_to_hardship_team(self):
        c = request(circumstances=[dict(category='job_loss', status='present', description='Lost job in July')])
        self.assertEqual(route(workflow(c)), 'hardship_support')
        arrears = request(borrower_name='Sam Rivera', mortgage_number='MTG-70032', property_postal_code='B3H 4R2')
        w = workflow(arrears)
        self.assertIn('ARR-001', rule_ids(w))
        self.assertEqual(route(w), 'hardship_support')

    def test_denied_hardship_is_not_hardship(self):
        c = request(circumstances=[dict(category='job_loss', status='absent', description='Caller says income is stable')])
        self.assertEqual(route(workflow(c)), 'ready_to_process')

    def test_legal_notice_is_urgent(self):
        c = request(circumstances=[dict(category='legal_notice', status='present', description='Power of sale notice')])
        w = workflow(c, 'hardship')
        self.assertEqual(w['service_request_packet']['priority'], 'urgent')
        self.assertIn('Copy of any legal notice received', [i['item'] for i in w['document_checklist']['items']])

    def test_security_beats_hardship(self):
        c = request(caller_role='other_third_party', circumstances=[dict(category='job_loss', status='present', description='Lost job')])
        self.assertEqual(route(workflow(c)), 'security_review')

    def test_specialist_request_types(self):
        for kind in ['rate_term_change', 'life_event', 'other']:
            with self.subTest(kind=kind):
                self.assertEqual(route(workflow(request(changes=dict(new_payment_frequency='not specified')), kind)), 'specialist_review')

    def test_blank_start_is_not_routed_to_specialist(self):
        self.assertEqual(build_initial_workflow_state()['security_hardship_gate']['final_routing_decision'], 'needs_documents')

    def test_renewal_window_note(self):
        w = r.apply_servicing_rules(request(mortgage_number='MTG-93006'), r.validate_required_fields(request()),
                                    dict(request_type='account_information', priority='low', priority_rationale='x'), today=date(2026, 10, 1))
        self.assertTrue(any('matures 2026-12-01' in n for n in w['servicing_notes']))

    def test_skip_payment_rules(self):
        allowed = workflow(request(changes=dict(new_payment_frequency='not specified', skip_payment=True)))
        self.assertTrue(any('skip one payment' in n for n in allowed['servicing_decision']['servicing_notes']))
        c = request(borrower_name='Priya Shah', mortgage_number='MTG-61845', property_postal_code='V5K 0A1',
                    changes=dict(new_payment_frequency='not specified', skip_payment=True))
        self.assertIn('SKIP-001', rule_ids(workflow(c)))

    def test_invalid_amounts_rejected(self):
        for value in [-5, float('nan'), float('inf')]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                ServiceRequest.model_validate(request(changes=dict(prepayment_amount_cad=value)))
        self.assertIsNone(ServiceRequest.model_validate(request(changes=dict(prepayment_amount_cad=0))).requested_changes.prepayment_amount_cad)

    def test_schemas_are_accepted_by_gemini(self):
        # Same conversion the SDK runs before every structured-output request.
        from google.genai import _transformers
        from schemas import RequestClassification
        client = NS(vertexai=False)
        for model in (ServiceRequest, RequestClassification, s.FrameObservation):
            with self.subTest(model=model.__name__):
                self.assertIsNotNone(_transformers.t_schema(client, model))


class ServerStateTests(unittest.TestCase):
    def test_account_details_hidden_until_verified(self):
        session = s.IntakeSession('test')
        s._attach_mortgage_from_request(session, workflow(request(property_postal_code='not specified')))
        ui = s._state_from_workflow(session, workflow(request(property_postal_code='not specified')))
        self.assertNotIn('balance', ui['fields'])
        self.assertEqual(ui['servicing_notes'], [])
        s._attach_mortgage_from_request(session, workflow(request()))
        ui = s._state_from_workflow(session, workflow(request()))
        self.assertIn('balance', ui['fields'])

    def test_mortgage_corrections_update_and_clear_record(self):
        session = s.IntakeSession('test')
        for number in ['MTG-52290', 'MTG-00000', 'not specified']:
            s._attach_mortgage_from_request(session, workflow(request(mortgage_number=number)))
            if number == 'not specified':
                self.assertIsNone(session.mortgage_record)
            else:
                self.assertEqual(session.mortgage_record['mortgage_number'], number)

    def test_postal_code_never_shown(self):
        ui = s._state_from_workflow(s.IntakeSession('t'), workflow(request()))
        self.assertNotIn('M4C', json.dumps(ui['fields']))

    def test_dialogue_retains_questions_and_ids(self):
        session = s.IntakeSession('test')
        s.append_turn(session, 'Agent', 'What is your mortgage number?', 'q1')
        s.append_turn(session, 'Caller', 'MTG-40117', 'a1')
        text = s._dialogue_text(session)
        self.assertIn('[q1] Agent: What is your mortgage number?', text)
        self.assertIn('[a1] Caller: MTG-40117', text)

    def test_agent_reply_does_not_trigger_duplicate_extraction(self):
        session = s.IntakeSession('revision')
        s.append_turn(session, 'Caller', 'I want to prepay', 'u1')
        revision = session.revision
        s.append_turn(session, 'Agent', 'How much?', 'a1')
        self.assertEqual(session.revision, revision)

    def test_turn_retry_deduplicates_only_identifier(self):
        session = s.IntakeSession('test')
        s.append_turn(session, 'Caller', 'Yes', '1'); s.append_turn(session, 'Caller', 'No', '2'); s.append_turn(session, 'Caller', 'No', '2')
        self.assertEqual(len(session.transcript), 2)

    def test_empty_progress(self):
        ui = s._state_from_workflow(s.IntakeSession('test'), build_initial_workflow_state())
        self.assertEqual(ui['progress'], 0)

    def test_complete_request_progress(self):
        ui = s._state_from_workflow(s.IntakeSession('test'), workflow(request()))
        self.assertEqual(ui['progress'], 100)

    def test_redacts_long_numbers(self):
        self.assertEqual(s.redact_numbers('Account 12345678 transit 00123'), 'Account •••5678 transit 00123')


class ScenarioToolTests(unittest.TestCase):
    def verified(self):
        return s.IntakeSession('scenario', mortgage_record=lookup_mortgage('MTG-40117', 'Maya Singh', 'M4C 1B5'))

    def test_requires_verified_caller(self):
        for record in [None, lookup_mortgage('MTG-40117', 'Maya Singh', '')]:
            session = s.IntakeSession('x', mortgage_record=record)
            self.assertFalse(s._show_payment_scenario(session, dict(prepayment_amount=5000))['charted'])
            self.assertIsNone(session.scenario)

    def test_charts_and_versions(self):
        session = self.verified()
        first = s._show_payment_scenario(session, dict(new_frequency='accelerated_bi_weekly'))
        self.assertTrue(first['charted']); self.assertGreater(first['interest_saved'], 30000)
        second = s._show_payment_scenario(session, dict(prepayment_amount=10000))
        self.assertTrue(second['charted']); self.assertEqual(session.scenario['version'], 2)

    def test_rejects_bad_input(self):
        session = self.verified()
        for args in [{}, dict(new_frequency='monthly'), dict(new_frequency='yearly'), dict(new_payment_amount=-1),
                     dict(new_payment_amount='abc'), dict(new_payment_amount=100), dict(prepayment_amount=10_000_000)]:
            with self.subTest(args=args):
                self.assertFalse(s._show_payment_scenario(session, args)['charted'])
        self.assertIsNone(session.scenario)

    def test_losing_verification_clears_chart(self):
        session = self.verified()
        s._show_payment_scenario(session, dict(prepayment_amount=5000))
        s._attach_mortgage_from_request(session, workflow(request(property_postal_code='K1N 6N5')))
        self.assertIsNone(session.scenario)


class AsyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_exact_frozen_frame_reverified_despite_new_arrival(self):
        session = s.IntakeSession('frame', last_frame=b'FRAME_A', last_frame_id='a', last_frame_at=time.monotonic())
        async def model(**kwargs):
            self.assertEqual(kwargs['contents'][0].inline_data.data, b'FRAME_A')
            session.last_frame = b'FRAME_B'
            return NS(text=json.dumps(dict(observation='Blank wall', supports_caller_description=False, document_types=[])))
        with patch.object(s, '_client', return_value=NS(aio=NS(models=NS(generate_content=model)))):
            result = await s._pin_document_photo(session, dict(observation='Void cheque', confirmed=True, caller_description='A void cheque'))
        photo = session.evidence_photos[0]
        self.assertEqual(base64.b64decode(photo['data_url'].split(',')[1]), b'FRAME_A')
        self.assertEqual(photo['caption'], 'Blank wall')
        self.assertFalse(result['confirmed']); self.assertIn('unconfirmed', session.camera_notes[0])

    async def test_capture_caption_is_redacted_and_types_filtered(self):
        session = s.IntakeSession('frame', last_frame=b'FRAME', last_frame_at=time.monotonic())
        async def model(**kwargs):
            return NS(text=json.dumps(dict(observation='Void cheque, account 123456789', supports_caller_description=True, document_types=['void_cheque', 'damage_photo'])))
        with patch.object(s, '_client', return_value=NS(aio=NS(models=NS(generate_content=model)))):
            result = await s._pin_document_photo(session, dict(caller_description='void cheque', observation='x', confirmed=True))
        self.assertTrue(result['confirmed'])
        self.assertNotIn('123456789', session.evidence_photos[0]['caption'])
        self.assertEqual(session.evidence_photos[0]['document_types'], ['void_cheque'])

    async def test_without_caller_description_capture_is_unconfirmed(self):
        session = s.IntakeSession('frame', last_frame=b'FRAME', last_frame_at=time.monotonic())
        async def model(**kwargs): return NS(text=json.dumps(dict(observation='Letter', supports_caller_description=True, document_types=[])))
        with patch.object(s, '_client', return_value=NS(aio=NS(models=NS(generate_content=model)))):
            self.assertFalse((await s._pin_document_photo(session, {}))['confirmed'])

    async def test_stale_frames_and_photo_limits(self):
        session = s.IntakeSession('frame', last_frame=b'OLD', last_frame_at=time.monotonic() - 30)
        self.assertFalse((await s._pin_document_photo(session, {}))['pinned'])
        session.last_frame_at = time.monotonic(); session.evidence_photos = [{}] * s.MAX_PHOTOS
        self.assertFalse((await s._pin_document_photo(session, {}))['pinned'])

    async def test_camera_off_clears_live_frame_but_retains_pinned_photos(self):
        photo = {'id': 'pinned'}
        session = s.IntakeSession('camera', camera_enabled=True, last_frame=b'frame', last_frame_id='frame-id', last_frame_at=time.monotonic(), evidence_photos=[photo])
        s.set_camera_mode(session, False)
        self.assertFalse(session.camera_enabled); self.assertIsNone(session.last_frame)
        self.assertEqual(session.evidence_photos, [photo])
        self.assertFalse((await s._pin_document_photo(session, {}))['pinned'])

    async def test_camera_state_turns_inform_model_without_caller_facts(self):
        session = s.IntakeSession('camera-wire', owner='owner'); s.sessions[session.session_id] = session
        messages = [dict(type='camera_state', enabled=True), dict(type='camera_state', enabled=False), dict(type='close')]
        delivered = []
        class WS:
            headers = {'host': '127.0.0.1:4188', 'origin': 'http://127.0.0.1:4188'}
            client = NS(host='127.0.0.1'); url = NS(scheme='ws'); cookies = {'intake_owner': 'owner'}; query_params = {'session_id': 'camera-wire'}
            async def accept(self): pass
            async def close(self, **kw): pass
            async def send_json(self, payload): pass
            async def receive_text(self): return json.dumps(messages.pop(0))
        class Live:
            async def send_client_content(self, **kw): delivered.append(kw)
            async def receive(self):
                await asyncio.Event().wait()
                yield None
        class CM:
            async def __aenter__(self): return Live()
            async def __aexit__(self, *args): pass
        fake = NS(aio=NS(live=NS(connect=lambda **kw: CM())))
        with patch.object(s, '_has_api_key', return_value=True), patch.object(s, '_live_client', return_value=fake):
            await asyncio.wait_for(s.live_voice(WS()), 1)
        self.assertEqual([x['turn_complete'] for x in delivered], [False, False])
        self.assertIn('STATE: ON', delivered[0]['turns'].parts[0].text)
        self.assertIn('STATE: OFF', delivered[1]['turns'].parts[0].text)
        self.assertEqual(session.transcript, [])
        s.sessions.pop(session.session_id)

    async def test_workflow_reruns_if_facts_change_in_flight(self):
        session = s.IntakeSession('race'); s.append_turn(session, 'Caller', 'Original', 'one')
        calls = []
        async def run(text, **kwargs):
            calls.append(text)
            if len(calls) == 1: s.append_turn(session, 'Caller', 'Correction', 'two')
            return workflow(request(request_summary='Correction' if len(calls) == 2 else 'Original'))
        with patch.object(s, 'run_request_workflow', side_effect=run): result = await s._run_workflow_cached(session)
        self.assertEqual(len(calls), 2); self.assertEqual(result['normalized_request']['request_summary'], 'Correction')

    async def test_cleanup_deletes_frames_and_cancels_jobs(self):
        session = s.IntakeSession('expired', updated_at=time.monotonic() - s.SESSION_TTL - 1, last_frame=b'image', evidence_photos=[{}])
        job = asyncio.create_task(asyncio.sleep(100)); session.tasks.add(job); s.sessions[session.session_id] = session
        await s.cleanup_sessions()
        self.assertNotIn('expired', s.sessions); self.assertTrue(job.cancelled()); self.assertIsNone(session.last_frame); self.assertEqual(session.evidence_photos, [])

    async def test_slow_workflow_does_not_block_video_audio_or_close(self):
        session = s.IntakeSession('transport', owner='owner'); s.sessions[session.session_id] = session
        started = asyncio.Event(); media = []
        class WS:
            headers = {'host': '127.0.0.1:4188', 'origin': 'http://127.0.0.1:4188'}
            client = NS(host='127.0.0.1'); url = NS(scheme='ws'); cookies = {'intake_owner': 'owner'}; query_params = {'session_id': 'transport'}
            def __init__(self): self.i = 0; self.sent = []
            async def accept(self): pass
            async def close(self, **kw): pass
            async def send_json(self, payload): self.sent.append(payload)
            async def receive_text(self):
                messages = [dict(type='text', text='Test request', id='1'), dict(type='video', data=base64.b64encode(b'\xff\xd8\xffTEST').decode()), dict(type='audio', data='AAA='), dict(type='close')]
                if self.i == 1: await started.wait()
                value = messages[self.i]; self.i += 1; return json.dumps(value)
        class Live:
            async def send_client_content(self, **kw): pass
            async def send_realtime_input(self, **kw): media.extend(kw)
            async def receive(self):
                await asyncio.Event().wait()
                yield None
        class CM:
            async def __aenter__(self): return Live()
            async def __aexit__(self, *args): pass
        async def run(*args, **kw): started.set(); await asyncio.Event().wait()
        fake = NS(aio=NS(live=NS(connect=lambda **kw: CM())))
        with patch.object(s, '_has_api_key', return_value=True), patch.object(s, '_live_client', return_value=fake), patch.object(s, 'run_request_workflow', side_effect=run):
            ws = WS(); await asyncio.wait_for(s.live_voice(ws), 1)
        self.assertEqual(media, ['video', 'audio']); self.assertFalse(session.camera_enabled); self.assertIsNone(session.live_socket); self.assertEqual(session.transcript[0]['id'], '1')
        s.sessions.pop('transport')


class AccessTests(unittest.TestCase):
    def setUp(self):
        s.sessions.clear(); self.client = TestClient(s.app, base_url='http://127.0.0.1:4188'); self.headers = {'Origin': 'http://127.0.0.1:4188'}
    def tearDown(self): self.client.close(); s.sessions.clear()
    def create(self): return self.client.post('/api/sessions', headers=self.headers).json()['session_id']
    def test_owner_cookie_required(self):
        sid = self.create(); self.assertEqual(self.client.get('/api/sessions/' + sid).status_code, 200)
        with TestClient(s.app, base_url='http://127.0.0.1:4188') as other:
            self.assertEqual(other.get('/api/sessions/' + sid).status_code, 404)
    def test_origin_host_and_missing_origin_rejected(self):
        for headers in [{}, {'Origin': 'https://evil.example'}, {'Origin': 'http://evil.example', 'Host': 'evil.example'}]:
            self.assertEqual(self.client.post('/api/sessions', headers=headers).status_code, 403)
    def test_websocket_rejects_origin_and_wrong_owner(self):
        from starlette.websockets import WebSocketDisconnect
        sid = self.create()
        with self.assertRaises(WebSocketDisconnect):
            with self.client.websocket_connect('/ws/live?session_id=' + sid, headers={'Origin': 'https://evil.example'}): pass
        self.client.cookies.clear()
        with self.assertRaises(WebSocketDisconnect):
            with self.client.websocket_connect('/ws/live?session_id=' + sid, headers=self.headers): pass
    def test_reset_deletes_old_call(self):
        old = self.create(); s.append_turn(s.sessions[old], 'Caller', 'Old confidential request')
        self.assertEqual(self.client.delete('/api/sessions/' + old, headers=self.headers).status_code, 200)
        new = self.create(); self.assertNotEqual(old, new)
        self.assertEqual(self.client.get('/api/sessions/' + old).status_code, 404)
        self.assertEqual(len(self.client.get('/api/sessions/' + new).json()['state']['transcript']), 1)
    def test_packet_download_contains_manifest_image_and_scenario(self):
        sid = self.create(); session = s.sessions[sid]
        session.evidence_photos.append(dict(id='photo1', frame_id='f1', data_url=s._data_url(b'JPEG', 'image/jpeg'), caption='Fixture', confirmed=False, caller_description='', captured_at='2026-09-18', document_types=[]))
        session.mortgage_record = lookup_mortgage('MTG-40117', 'Maya Singh', 'M4C 1B5')
        s._show_payment_scenario(session, dict(prepayment_amount=5000))
        response = self.client.get('/api/sessions/' + sid + '/packet')
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            self.assertEqual(archive.read('documents/photo1.jpg'), b'JPEG')
            md = archive.read('request.md').decode()
            self.assertIn('documents/photo1.jpg', md)
            self.assertIn('Payment scenario shown to the caller', md)
            self.assertNotIn('data_url', json.loads(archive.read('documents.json'))[0])
            self.assertEqual(json.loads(archive.read('payment-scenario.json'))['version'], 1)
    def test_session_limit_and_source_not_served(self):
        for _ in range(4): self.create()
        self.assertEqual(self.client.post('/api/sessions', headers=self.headers).status_code, 429)
        self.assertEqual(self.client.get('/server.py').status_code, 404)


if __name__ == '__main__': unittest.main(verbosity=2)
