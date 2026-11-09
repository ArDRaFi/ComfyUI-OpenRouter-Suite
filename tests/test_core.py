"""Offline unit tests. Run from the repo root with: python -m unittest discover tests"""

import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("ORS_USER_DIR", tempfile.mkdtemp())

from openrouter_suite import capabilities as caps  # noqa: E402
from openrouter_suite import catalog, client, config  # noqa: E402

with open(catalog.SNAPSHOT_PATH, encoding="utf-8") as f:
    SNAPSHOT = json.load(f)


class Retry(unittest.TestCase):
    def test_rate_limit_always_retried(self):
        self.assertTrue(client.should_retry(429, "/videos", billable=True))

    def test_image_502_retried_because_unbilled(self):
        self.assertTrue(client.should_retry(502, "/images", billable=True))

    def test_billable_server_error_not_retried(self):
        self.assertFalse(client.should_retry(500, "/videos", billable=True))
        self.assertFalse(client.should_retry(502, "/videos", billable=True))

    def test_read_errors_retried(self):
        self.assertTrue(client.should_retry(503, "/videos/x", billable=False))

    def test_client_errors_never_retried(self):
        self.assertFalse(client.should_retry(400, "/images", billable=False))
        self.assertFalse(client.should_retry(402, "/images", billable=True))


class Errors(unittest.TestCase):
    def test_insufficient_credits(self):
        body = json.dumps({"error": {"code": 402, "message": "Need more"}})
        self.assertIn("Not enough OpenRouter credits", client.describe_error(402, body))

    def test_provider_detail(self):
        body = json.dumps({"error": {"message": "bad", "metadata": {"provider_name": "Seed", "raw": "too many images"}}})
        message = client.describe_error(400, body)
        self.assertIn("[Seed]", message)
        self.assertIn("too many images", message)

    def test_non_json_body(self):
        self.assertIn("gateway", client.describe_error(502, "gateway exploded"))

    def test_key_is_redacted(self):
        os.environ[config.ENV_KEY] = "sk-or-secret"
        try:
            self.assertNotIn("sk-or-secret", client.describe_error(400, "echo sk-or-secret"))
        finally:
            del os.environ[config.ENV_KEY]

    def test_base_url_override_is_loopback_only(self):
        os.environ["ORS_TEST_BASE_URL"] = "https://evil.example/api"
        try:
            self.assertEqual(client.base_url(), "https://openrouter.ai/api/v1")
        finally:
            del os.environ["ORS_TEST_BASE_URL"]


class SSE(unittest.TestCase):
    def test_event_split_across_chunks(self):
        parser = client.SSEParser()
        self.assertEqual(parser.feed(b'data: {"a"'), [])
        self.assertEqual(parser.feed(b":1}\n\n"), ['{"a":1}'])

    def test_comments_ignored(self):
        self.assertEqual(client.SSEParser().feed(b": PROCESSING\n\ndata: x\n\n"), ["x"])

    def test_crlf(self):
        self.assertEqual(client.SSEParser().feed(b"data: x\r\n\r\n"), ["x"])

    def test_multibyte_character_split(self):
        parser = client.SSEParser()
        encoded = "data: é\n\n".encode()
        self.assertEqual(parser.feed(encoded[:7]), [])
        self.assertEqual(parser.feed(encoded[7:]), ["é"])


class Merge(unittest.TestCase):
    OLD = {"image": {"a/x": {"params": {"quality": {"type": "enum", "values": ["low", "high"]}}, "inputs": []}, "a/gone": {"params": {}, "inputs": []}}}

    def test_new_model_reported(self):
        new = {"image": {**self.OLD["image"], "a/new": {"params": {}, "inputs": []}}}
        _, added = catalog.merge(self.OLD, new)
        self.assertEqual(added, ["a/new"])

    def test_retired_model_kept_and_flagged(self):
        merged, _ = catalog.merge(self.OLD, {"image": {"a/x": self.OLD["image"]["a/x"]}})
        self.assertTrue(merged["image"]["a/gone"]["retired"])

    def test_removed_option_value_kept(self):
        new = {"image": {"a/x": {"params": {"quality": {"type": "enum", "values": ["high"]}}, "inputs": []}}}
        merged, _ = catalog.merge(self.OLD, new)
        self.assertEqual(set(merged["image"]["a/x"]["params"]["quality"]["values"]), {"low", "high"})

    def test_failed_section_keeps_old_models(self):
        merged, added = catalog.merge(self.OLD, {"fetched_at": 1})
        self.assertEqual(merged["image"], self.OLD["image"])
        self.assertEqual(added, [])

    def test_first_load_does_not_announce_everything(self):
        _, added = catalog.merge({}, self.OLD)
        self.assertEqual(added, [])

    def test_returning_model_is_unretired_and_announced(self):
        old = {"image": {"a/x": {"params": {}, "inputs": [], "retired": True}}}
        merged, added = catalog.merge(old, {"image": {"a/x": {"params": {}, "inputs": []}}})
        self.assertNotIn("retired", merged["image"]["a/x"])
        self.assertEqual(added, ["a/x"])

    def test_version_ignores_fetch_time(self):
        self.assertEqual(catalog.version_of({"fetched_at": 1, **self.OLD}), catalog.version_of({"fetched_at": 2, **self.OLD}))


class ImageCapabilities(unittest.TestCase):
    def test_slot_limit_matches_catalog_for_every_model(self):
        for slug, entry in SNAPSHOT["image"].items():
            published = entry["params"].get("input_references")
            if published:
                self.assertEqual(caps.image_caps(slug, entry, {}).max_refs, published["max"], slug)

    def test_minimum_references(self):
        self.assertEqual(caps.image_caps("krea/k3-edit", SNAPSHOT["image"]["krea/k3-edit"], {}).min_refs, 1)

    def test_text_only_model_has_no_slots(self):
        self.assertEqual(caps.image_caps("x/y", {"params": {"n": {"type": "range", "min": 1, "max": 1}}, "inputs": ["text"]}, {}).max_refs, 0)

    def test_unknown_future_parameter_gets_widget(self):
        entry = {"params": {"sharpness": {"type": "enum", "values": ["soft", "crisp"]}}, "inputs": ["text"]}
        self.assertEqual([p.name for p in caps.image_caps("x/y", entry, {}).params], ["sharpness"])

    def test_gif_hidden(self):
        entry = {"params": {"output_format": {"type": "enum", "values": ["png", "gif"]}}, "inputs": ["text"]}
        self.assertEqual(caps.image_caps("x/y", entry, {}).params[0].values, ["png"])

    def test_vector_only_detected(self):
        self.assertTrue(caps.is_vector_only(SNAPSHOT["image"]["recraft/recraft-v4-vector"]))
        self.assertFalse(caps.is_vector_only(SNAPSHOT["image"]["openai/gpt-image-2"]))

    def test_missing_data_uses_default(self):
        self.assertEqual(caps.image_caps("x/y", {"params": {}, "inputs": ["text", "image"]}, {}).max_refs, caps.DEFAULT_REF_IMAGES)


class VideoCapabilities(unittest.TestCase):
    def test_slots_match_catalog_for_every_model(self):
        for slug, entry in SNAPSHOT["video"].items():
            capability = caps.video_caps(slug, entry, {})
            self.assertEqual(capability.first_frame, "first_frame" in entry["frames"], slug)
            self.assertEqual(capability.last_frame, "last_frame" in entry["frames"], slug)
            if "video" not in entry["inputs"]:
                self.assertEqual(capability.ref_videos, 0, slug)
                self.assertFalse(capability.source_video, slug)
            if "audio" not in entry["inputs"]:
                self.assertEqual(capability.ref_audios, 0, slug)

    def test_first_only(self):
        capability = caps.video_caps("x/y", {"frames": ["first_frame"], "durations": [5], "inputs": ["text"]}, {})
        self.assertTrue(capability.first_frame)
        self.assertFalse(capability.last_frame)
        self.assertFalse(capability.needs_input)

    def test_edit_model(self):
        capability = caps.video_caps("x/y", {"frames": [], "durations": None, "inputs": ["text", "video"]}, {})
        self.assertTrue(capability.source_video and capability.needs_input)
        self.assertEqual(capability.ref_videos, 0)

    def test_new_model_gets_defaults(self):
        capability = caps.video_caps("new/model", {"frames": [], "durations": [5], "inputs": ["text", "image", "video", "audio"]}, {})
        self.assertEqual((capability.ref_images, capability.ref_videos, capability.ref_audios), (4, 1, 1))

    def test_override_wins_and_wildcards_match(self):
        table = {"bytedance/seedance-2.*": {"ref_images": 9, "ref_videos": 3, "ref_audios": 3}}
        capability = caps.video_caps("bytedance/seedance-2.0", SNAPSHOT["video"]["bytedance/seedance-2.0"], table)
        self.assertEqual((capability.ref_images, capability.ref_videos, capability.ref_audios), (9, 3, 3))

    def test_override_can_remove_references(self):
        capability = caps.video_caps("x/y", {"frames": ["first_frame"], "durations": [5], "inputs": ["text", "image"]}, {"x/*": {"ref_images": 0}})
        self.assertEqual(capability.ref_images, 0)


class Hosting(unittest.TestCase):
    def test_plain_text_response(self):
        from openrouter_suite.media import hosting
        self.assertEqual(hosting.parse_text_url(" https://x.test/a.mp4\n"), "https://x.test/a.mp4")

    def test_uguu_response(self):
        from openrouter_suite.media import hosting
        self.assertEqual(hosting.parse_uguu('{"success":true,"files":[{"url":"https://u.test/a.mp4"}]}'), "https://u.test/a.mp4")

    def test_real_hosts_used_outside_tests(self):
        from openrouter_suite.media import hosting
        self.assertEqual(hosting._active_hosts(), hosting.HOSTS)


class Config(unittest.TestCase):
    def test_status_never_contains_full_key(self):
        config.set_api_key("sk-or-abcdefgh")
        try:
            status = config.key_status()
            self.assertEqual(status["last4"], "efgh")
            self.assertNotIn("sk-or-abcdefgh", json.dumps(status))
        finally:
            config.set_api_key("")


class Srt(unittest.TestCase):
    def test_format(self):
        try:
            from openrouter_suite.media.audio import to_srt
        except ImportError:
            self.skipTest("media dependencies unavailable")
        self.assertIn("00:01:01,500 --> 00:01:02,000", to_srt([{"start": 61.5, "end": 62.0, "text": " hi"}]))


if __name__ == "__main__":
    unittest.main()
