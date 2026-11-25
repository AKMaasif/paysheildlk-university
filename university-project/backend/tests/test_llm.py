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


def test_meta_success_does_not_call_grok():
    def behavior(url):
        if "api.meta.ai" in url:
            return {"choices": [{"message": {"content": "Feed restated."}}]}
        raise AssertionError(url)

    transport = Scripted(behavior)
    router = LLMRouter(meta_api_key="m-key", xai_api_key="x-key", transport=transport)
    result = router.complete(system="s", user="facts", purpose="banking")
    assert result.provider == "meta"
    assert result.fell_back is False
    assert result.text == "Feed restated."
    assert len(transport.calls) == 1


def test_a_rate_limit_on_meta_falls_back_to_grok():
    def behavior(url):
        if "api.meta.ai" in url:
            raise LLMError("meta", "rate", status=429)
        if "api.x.ai" in url:
            return {"choices": [{"message": {"content": "Grok note."}}]}
        raise AssertionError(url)

    transport = Scripted(behavior)
    router = LLMRouter(meta_api_key="m-key", xai_api_key="x-key", xai_model="grok-test", transport=transport)
    result = router.complete(system="s", user="facts", purpose="risk")
    assert result.provider == "grok"
    assert result.model == "grok-test"
    assert result.fell_back is True
    assert result.fallback_from == "meta"
    assert result.text == "Grok note."
    grok_call = transport.calls[1]
    assert grok_call["url"] == "https://api.x.ai/v1/chat/completions"
    assert grok_call["headers"]["Authorization"] == "Bearer x-key"
    assert grok_call["json"]["model"] == "grok-test" and grok_call["json"]["max_tokens"] >= 1024
    assert grok_call["timeout"] >= 30


def test_each_provider_gets_only_its_own_key():
    def behavior(url):
        if "api.meta.ai" in url:
            raise LLMError("meta", "down", status=503)
        return {"choices": [{"message": {"content": "ok"}}]}

    transport = Scripted(behavior)
    LLMRouter(meta_api_key="m-key", xai_api_key="x-key", transport=transport).complete(system="s", user="facts", purpose="risk")
    assert transport.calls[0]["headers"]["Authorization"] == "Bearer m-key"
    assert transport.calls[1]["headers"]["Authorization"] == "Bearer x-key"
    assert all("m-key" not in str(call["json"]) and "x-key" not in str(call["json"]) and "m-key" not in call["url"] and "x-key" not in call["url"] for call in transport.calls)


def test_with_only_a_grok_key_grok_is_the_first_choice():
    def behavior(url):
        assert "api.x.ai" in url, url
        return {"choices": [{"message": {"content": "Grok alone."}}]}

    router = LLMRouter(xai_api_key="x-key", transport=Scripted(behavior))
    result = router.complete(system="s", user="facts", purpose="risk")
    assert (result.provider, result.fell_back, result.fallback_from) == ("grok", False, None)


def test_meta_timeout_falls_back_to_mock():
    def behavior(url):
        raise LLMError("meta", "timeout")

    transport = Scripted(behavior)
    router = LLMRouter(meta_api_key="m-key", transport=transport)
    result = router.complete(system="s", user="swapped: no", purpose="telecom")
    assert result.provider == "mock"
    assert result.fell_back is True
    assert result.fallback_from == "meta"


def test_grok_failure_with_no_other_provider_uses_mock():
    def behavior(url):
        raise LLMError("grok", "unavailable", status=503)

    transport = Scripted(behavior)
    router = LLMRouter(xai_api_key="x-key", transport=transport)
    result = router.complete(system="s", user="facts", purpose="case")
    assert result.provider == "mock"
    assert result.fell_back is True
    assert result.fallback_from == "grok"
    assert "incident report" in result.text.lower() or "record" in result.text.lower()
