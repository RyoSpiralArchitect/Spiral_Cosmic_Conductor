# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 Ryo Higa / SpiralReality. All rights reserved.
from __future__ import annotations

import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from conductor import ContractError, Ledger
from conductor.adapters import AdapterError, LocalChatWorker, ScriptedWorker, workers_from_config
from conductor.scenarios import capacity_task
from conductor.types import Dispatch


class Handler(BaseHTTPRequestHandler):
    mode = "good"
    seen = []

    def log_message(self, *args):
        pass

    def do_POST(self):
        raw = self.rfile.read(int(self.headers["Content-Length"]))
        req = json.loads(raw)
        Handler.seen.append((self.path, req))
        if Handler.mode == "redirect":
            self.send_response(302)
            self.send_header("Location", "http://192.0.2.1/not-allowed")
            self.end_headers()
            return
        if Handler.mode == "http_error":
            self.send_response(500)
            self.end_headers()
            self.wfile.write(b"synthetic server error")
            return
        view = json.loads(req["messages"][1]["content"])["view"]
        corrupt = {"workers"} if Handler.mode == "noisy_first" and req["model"] == "fixture-a" else set()
        reply = ScriptedWorker("http-fixture", corrupt=corrupt).propose(Dispatch("unused", view, "http-fixture", "proposer"), 2)
        if Handler.mode == "revise_missing":
            reply.patch["ops"][0]["op"] = "revise_claim"
        content = json.dumps(reply.patch)
        if Handler.mode == "bad_json":
            content = "not a JSON object"
        if Handler.mode == "bad_patch":
            content = '{"ops":[{"op":"verify"}]}'
        out = {"choices": [{"finish_reason": "length" if Handler.mode == "truncated" else "stop",
                            "message": {"content": content}}],
               "usage": {"prompt_tokens": 123, "completion_tokens": 45}}
        if Handler.mode == "tool":
            out["choices"][0]["message"]["tool_calls"] = [{"name": "shell"}]
        body = json.dumps(out).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class LocalAdapterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}/v1"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def setUp(self):
        Handler.mode, Handler.seen = "good", []
        self.ledger = Ledger(":memory:", capacity_task())
        self.addCleanup(self.ledger.close)
        self.dispatch = self.ledger.issue("test-local", "proposer", {"workers"})
        self.worker = LocalChatWorker("test-local", self.base, "fixture-model")

    def test_loopback_http_protocol_and_commit(self):
        reply = self.worker.propose(self.dispatch, 2)
        self.assertEqual(reply.usage.prompt_tokens, 123)
        self.assertEqual(reply.usage.completion_tokens, 45)
        self.assertGreater(reply.usage.request_bytes, 0)
        self.assertTrue(self.ledger.apply(self.dispatch.ticket, reply.patch).accepted)
        self.assertTrue(self.ledger.verify("workers").accepted)
        path, req = Handler.seen[-1]
        self.assertEqual(path, "/v1/chat/completions")
        self.assertEqual(req["model"], "fixture-model")
        self.assertEqual(req["response_format"], {"type": "json_object"})
        self.assertNotIn("tools", req)
        self.assertNotIn(self.dispatch.ticket, json.dumps(req))
        target = json.loads(req["messages"][1]["content"])["target_contract"]
        self.assertEqual(target, {"claim_id": "workers", "current_status": "missing",
                                  "required_op": "add_claim", "required_evidence_ids": ["current"],
                                  "value_spec": {"kind": "fact", "source": "current", "field": "workers"}})

    def test_wrong_operation_is_not_silently_rewritten(self):
        Handler.mode = "revise_missing"
        reply = self.worker.propose(self.dispatch, 2)
        self.assertEqual(reply.patch["ops"][0]["op"], "revise_claim")
        self.assertEqual(self.ledger.apply(self.dispatch.ticket, reply.patch).reason, "claim_does_not_exist")
        self.assertEqual(self.ledger.snapshot()["workers"]["status"], "missing")

    def test_unsupported_scope_or_role_never_reaches_transport(self):
        for role, writes in (("proposer", {"workers", "per_worker"}), ("critic", {"workers"})):
            with self.subTest(role=role):
                dispatch = self.ledger.issue("test-local", role, writes)
                with self.assertRaises(AdapterError):
                    self.worker.propose(dispatch, 2)
        self.assertEqual(Handler.seen, [])

    def test_configurable_json_mode_off(self):
        w = LocalChatWorker("test-local", self.base, "fixture-model", json_mode=False)
        w.propose(self.dispatch, 2)
        self.assertNotIn("response_format", Handler.seen[-1][1])

    def test_no_environment_proxy(self):
        with patch.dict("os.environ", {"http_proxy": "http://192.0.2.1:65500", "HTTP_PROXY": "http://192.0.2.1:65500", "no_proxy": ""}):
            worker = LocalChatWorker("test-local", self.base, "fixture-model")
            self.assertIsNotNone(worker.propose(self.dispatch, 2))

    def test_remote_and_ambiguous_addresses_rejected(self):
        for url in ("https://example.com:443/v1", "http://10.0.0.1:8080/v1", "http://localhost:8080/v1",
                    "http://127.0.0.1:8080/v1?redirect=1", "http://user:pw@127.0.0.1:8080/v1",
                    "http://127.0.0.1.evil:8080/v1", "file:///tmp/server", "http://127.0.0.1/v1"):
            with self.subTest(url=url), self.assertRaises(ContractError):
                LocalChatWorker("w", url, "test")

    def test_redirect_is_not_followed(self):
        Handler.mode = "redirect"
        with self.assertRaises(AdapterError):
            self.worker.propose(self.dispatch, 2)

    def test_incomplete_response_rejected(self):
        Handler.mode = "truncated"
        with self.assertRaises(AdapterError):
            self.worker.propose(self.dispatch, 2)

    def test_tool_call_rejected(self):
        Handler.mode = "tool"
        with self.assertRaises(AdapterError):
            self.worker.propose(self.dispatch, 2)

    def test_malformed_json_rejected(self):
        Handler.mode = "bad_json"
        with self.assertRaises(AdapterError):
            self.worker.propose(self.dispatch, 2)

    def test_bad_patch_rejected(self):
        Handler.mode = "bad_patch"
        with self.assertRaises(AdapterError):
            self.worker.propose(self.dispatch, 2)

    def test_http_error_fails_without_hidden_retry(self):
        Handler.mode = "http_error"
        with self.assertRaises(AdapterError):
            self.worker.propose(self.dispatch, 2)
        self.assertEqual(len(Handler.seen), 1)

    def test_zero_timeout_rejected(self):
        with self.assertRaises(AdapterError):
            self.worker.propose(self.dispatch, 0)

    def test_model_config_distinguishes_workers(self):
        cfg = {"fast": {"name": "fast", "base_url": self.base, "model": "m1"},
               "careful": {"name": "careful", "base_url": self.base, "model": "m2", "cost_units": 3}}
        fast, careful = workers_from_config(cfg)
        self.assertEqual((fast.model, careful.model), ("m1", "m2"))
        cfg["careful"]["name"] = "fast"
        with self.assertRaises(ContractError):
            workers_from_config(cfg)

    def test_two_local_adapter_instances_drive_complete_runtime(self):
        # This is a loopback HTTP integration test, NOT inference on real weights.
        from conductor import Conductor
        fast = LocalChatWorker("local-fast", self.base, "fixture-a")
        careful = LocalChatWorker("local-careful", self.base, "fixture-b", cost_units=3)
        out = Conductor(self.ledger, fast, careful).run()
        self.assertTrue(out["complete"])
        self.assertEqual(out["usage"]["prompt_tokens"], 123 * 6)
        contracts = {data["target_contract"]["claim_id"]: data["target_contract"]
                     for _, request in Handler.seen
                     for data in [json.loads(request["messages"][1]["content"])]}
        for cid in ("capacity", "can_deliver"):
            self.assertEqual(contracts[cid]["required_evidence_ids"], [])
            self.assertEqual(contracts[cid]["required_op"], "add_claim")
            self.assertNotIn("value", contracts[cid])

    def test_heterogeneous_model_repair_over_http(self):
        from conductor import Conductor
        Handler.mode = "noisy_first"
        fast = LocalChatWorker("local-fast", self.base, "fixture-a")
        careful = LocalChatWorker("local-careful", self.base, "fixture-b", cost_units=3)
        out = Conductor(self.ledger, fast, careful).run()
        self.assertTrue(out["complete"])
        self.assertEqual(out["workers_used"], ["local-careful", "local-fast"])
        self.assertEqual(out["usage"]["calls"], 9)
        self.assertIs(out["results"]["can_deliver"], True)
        repairs = [json.loads(request["messages"][1]["content"])["target_contract"]
                   for _, request in Handler.seen if request["model"] == "fixture-b"]
        self.assertEqual({r["claim_id"] for r in repairs}, {"workers", "capacity", "can_deliver"})
        self.assertTrue(all(r["current_status"] == "invalidated" and r["required_op"] == "revise_claim"
                            for r in repairs))
