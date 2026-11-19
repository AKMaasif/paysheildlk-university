from app.llm import LLMError, LLMRouter


class Scripted:
    def __init__(self, behavior):
        self.behavior = behavior
        self.calls = []

    def post_json(self, url, headers, json, timeout):
        self.calls.append({"url": url, "headers": headers, "json": json, "timeout": timeout})
        action = self.behavior(url)
        if isinstance(action, Exception):
            raise action
        return action


def test_mock_when_no_keys_are_set():
    transport = Scripted(lambda url: (_ for _ in ()).throw(AssertionError("network should not be used")))
    router = LLMRouter(transport=transport)
    result = router.complete(system="s", user="swapped: yes", purpose="telecom")
    assert result.provider == "mock"
    assert result.model == "mock-deterministic"
    assert result.fell_back is False
    assert "SIM change" in result.text
    assert transport.calls == []


def test_gemini_success_does_not_call_meta():
    def behavior(url):
        if "generativelanguage.googleapis.com" in url:
            return {"candidates": [{"content": {"parts": [{"text": "Feed restated."}]}}]}
        raise AssertionError(url)

    transport = Scripted(behavior)
    router = LLMRouter(gemini_api_key="g-key", meta_api_key="m-key", transport=transport)
    result = router.complete(system="s", user="facts", purpose="banking")
    assert result.provider == "gemini"
    assert result.fell_back is False
    assert result.text == "Feed restated."
    assert len(transport.calls) == 1


def test_rate_limit_falls_back_to_meta():
    def behavior(url):
        if "generativelanguage.googleapis.com" in url:
            raise LLMError("gemini", "rate", status=429)
        if "api.meta.ai" in url:
            return {"choices": [{"message": {"content": "Meta note."}}]}
        raise AssertionError(url)

    transport = Scripted(behavior)
    router = LLMRouter(
        gemini_api_key="g-key",
        meta_api_key="m-key",
        meta_model="muse-ember-1.0",
        transport=transport,
    )
    result = router.complete(system="s", user="facts", purpose="risk")
    assert result.provider == "meta"
    assert result.model == "muse-ember-1.0"
    assert result.fell_back is True
    assert result.fallback_from == "gemini"
    assert result.text == "Meta note."
    meta_call = transport.calls[1]
    assert meta_call["json"]["max_tokens"] >= 1024
    assert meta_call["json"]["model"] == "muse-ember-1.0"
    assert meta_call["timeout"] >= 30


def test_meta_timeout_falls_back_to_mock():
    def behavior(url):
        raise LLMError("meta", "timeout")

    transport = Scripted(behavior)
    router = LLMRouter(meta_api_key="m-key", transport=transport)
    result = router.complete(system="s", user="swapped: no", purpose="telecom")
    assert result.provider == "mock"
    assert result.fell_back is True
    assert result.fallback_from == "meta"


def test_gemini_failure_without_meta_uses_mock():
    def behavior(url):
        raise LLMError("gemini", "unavailable", status=503)

    transport = Scripted(behavior)
    router = LLMRouter(gemini_api_key="g-key", transport=transport)
    result = router.complete(system="s", user="facts", purpose="case")
    assert result.provider == "mock"
    assert result.fell_back is True
    assert "incident report" in result.text.lower() or "record" in result.text.lower()
