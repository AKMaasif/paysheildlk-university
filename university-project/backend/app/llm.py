import logging
import time
from dataclasses import dataclass

import httpx

log = logging.getLogger("payshield.llm")


class LLMError(Exception):
    def __init__(self, provider: str, message: str, status: int | None = None):
        self.provider = provider
        self.status = status
        super().__init__(message)


@dataclass
class LLMResult:
    text: str
    provider: str
    model: str
    latency_ms: int
    fell_back: bool = False
    fallback_from: str | None = None


class HttpxTransport:
    def post_json(self, url: str, headers: dict, json: dict, timeout: float) -> dict:
        try:
            response = httpx.post(url, headers=headers, json=json, timeout=timeout)
        except httpx.TimeoutException as exc:
            raise LLMError("http", "timeout") from exc
        except httpx.HTTPError as exc:
            raise LLMError("http", str(exc)) from exc
        if response.status_code == 429 or response.status_code >= 500:
            raise LLMError("http", response.text[:240], status=response.status_code)
        if response.status_code >= 400:
            raise LLMError("http", response.text[:300], status=response.status_code)
        return response.json()


def _mock_text(purpose: str, user: str) -> str:
    lowered = user.lower()
    if purpose == "telecom":
        if "swapped: yes" in lowered or "swapped: true" in lowered:
            return (
                "The operator feed records a SIM change close to this payment, and the handset "
                "and location lines are the ones returned by the tools. This note only restates the feed."
            )
        return (
            "The operator feed shows no SIM change inside the checked window. "
            "Handset and location, where present, do not add a recent swap."
        )
    if purpose == "banking":
        if "new_payee: yes" in lowered or "new payee" in lowered:
            return (
                "The account activity includes a payee that was added recently and a transfer "
                "to that payee. Amounts below are the ledger figures."
            )
        return (
            "The recent account activity is the list returned by the bank tool. "
            "Nothing in that list is being reclassified here."
        )
    if purpose == "risk":
        return (
            "The score is the sum of the factor table from the rule definitions, capped at 100. "
            "It is an input to the rule layer, which makes the hold, block, or release decision."
        )
    if purpose == "alert":
        return (
            "The customer is being asked on the registered device whether they made this payment. "
            "The SMS is a generic warning with no code and no link."
        )
    if purpose == "verification":
        return (
            "The customer said the payment was theirs. "
            "The enrolled security questions are the check before any release."
        )
    if purpose == "response":
        return (
            "The protective actions are a block on the transfer, a recorded SIM freeze request (not yet sent to the operator), "
            "and limits on the account. The case file records each one."
        )
    if purpose == "case":
        return (
            "The incident report lists the feeds, the rule decision, the customer answer, and the actions. "
            "It is the record a fraud desk would keep."
        )
    return "Note recorded from the tool results."


class LLMRouter:
    """Meta is the main model and Grok (xAI) is the fallback. Both speak the OpenAI-style chat-completions format. With
    neither key set, or when both fail, the deterministic mock writes the note, so a case never waits on a model."""

    def __init__(
        self,
        *,
        meta_api_key: str = "",
        meta_base_url: str = "https://api.meta.ai/v1",
        meta_model: str = "muse-ember-1.0",
        xai_api_key: str = "",
        xai_base_url: str = "https://api.x.ai/v1",
        xai_model: str = "grok-4.20-0309-non-reasoning",
        transport=None,
    ):
        self.meta_api_key = meta_api_key
        self.meta_base_url = meta_base_url.rstrip("/")
        self.meta_model = meta_model
        self.xai_api_key = xai_api_key
        self.xai_base_url = xai_base_url.rstrip("/")
        self.xai_model = xai_model
        self.transport = transport or HttpxTransport()

    def _providers(self) -> list[str]:
        """The configured providers in the order they are tried."""
        return [name for name, key in (("meta", self.meta_api_key), ("grok", self.xai_api_key)) if key]

    def complete(self, *, system: str, user: str, purpose: str) -> LLMResult:
        from app.privacy import guard

        # Every provider, the mock included, only ever sees masked text with no secrets.
        system = guard(system)
        user = guard(user)
        from app import faults

        providers = self._providers()
        if faults.is_on("llm_down"):
            # Drill: every provider fails. The rules engine writes the note and the case carries on.
            return self._mock(purpose, user, fell_back=True, fallback_from=providers[0] if providers else "meta")
        if not providers:
            return self._mock(purpose, user, fell_back=False, fallback_from=None)

        for position, name in enumerate(providers):
            try:
                result = self._meta(system, user) if name == "meta" else self._grok(system, user)
            except Exception as exc:
                log.warning("%s call failed for %s: %s", name, purpose, exc)
                continue
            if position > 0:
                result.fell_back = True
                result.fallback_from = providers[0]
            return result
        return self._mock(purpose, user, fell_back=True, fallback_from=providers[0])

    def _mock(self, purpose: str, user: str, fell_back: bool, fallback_from: str | None) -> LLMResult:
        started = time.perf_counter()
        text = _mock_text(purpose, user)
        return LLMResult(
            text=text,
            provider="mock",
            model="mock-deterministic",
            latency_ms=int((time.perf_counter() - started) * 1000),
            fell_back=fell_back,
            fallback_from=fallback_from,
        )

    def _chat(self, provider: str, base_url: str, api_key: str, model: str, system: str, user: str, timeout: float) -> LLMResult:
        """One OpenAI-style chat-completions call. The key goes in the Authorization header and nowhere else."""
        started = time.perf_counter()
        url = f"{base_url}/chat/completions"
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        # Reasoning models spend tokens thinking before they answer, so the completion budget stays large.
        body = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "max_tokens": 4096,
            "temperature": 0.2,
        }
        data = self.transport.post_json(url, headers, body, timeout=timeout)
        choices = data.get("choices") or []
        if not choices:
            raise LLMError(provider, "no choices")
        message = choices[0].get("message") or {}
        content = message.get("content") or ""
        if isinstance(content, list):
            chunks = []
            for part in content:
                if isinstance(part, dict):
                    chunks.append(part.get("text") or "")
                else:
                    chunks.append(str(part))
            content = "".join(chunks)
        text = str(content).strip()
        if not text:
            raise LLMError(provider, "empty content")
        return LLMResult(
            text=text,
            provider=provider,
            model=model,
            latency_ms=int((time.perf_counter() - started) * 1000),
        )

    def _meta(self, system: str, user: str) -> LLMResult:
        return self._chat("meta", self.meta_base_url, self.meta_api_key, self.meta_model, system, user, timeout=45)

    def _grok(self, system: str, user: str) -> LLMResult:
        return self._chat("grok", self.xai_base_url, self.xai_api_key, self.xai_model, system, user, timeout=45)
