"""공통 모델 계약과 외부 API 어댑터. 외부 API 키는 환경변수에서만 읽는다."""
import copy
import json
import os
import time
from email.utils import parsedate_to_datetime
from datetime import datetime, timezone
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen
from .ai import OllamaClient, prompt_messages, validate
from .time_budget import remaining


class ProviderError(RuntimeError):
    """설정/권한 오류: 같은 설정으로 재시도하지 않고 게임을 중단한다."""


class TransientProviderError(ValueError):
    def __init__(self,message,retry_after=None):
        super().__init__(message);self.retry_after=retry_after


def retry_after_seconds(value):
    if not value:return None
    try:
        try:seconds=float(value)
        except ValueError:seconds=(parsedate_to_datetime(value)-datetime.now(timezone.utc)).total_seconds()
        return max(0.,min(seconds,30.))
    except (ValueError,TypeError,OverflowError):return None


class CallLimitExceeded(ProviderError):
    pass


class ModelClient(Protocol):
    metrics: list
    def check(self): ...
    def generate(self, view, schema, feedback=None): ...


def wire_schema(schema):
    result = copy.deepcopy(schema)
    for spec in result["properties"].values():
        if "enum" in spec:
            spec["type"] = ["string", "null"] if None in spec["enum"] else "string"
    return result


def post_json(url, body, headers, timeout):
    request = Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json", **headers})
    try:
        with urlopen(request, timeout=timeout) as response:
            return json.load(response)
    except HTTPError as error:
        try:
            if error.code in (408,429) or 500<=error.code<600:
                raise TransientProviderError(f"API HTTP {error.code}: 일시적인 요청 실패",retry_after_seconds(error.headers.get('Retry-After') if error.headers else None)) from None
            raise ProviderError(f"API HTTP {error.code}: 키·모델·요청 설정을 확인하세요") from None
        finally:error.close()
    except (URLError, TimeoutError, OSError):
        raise TransientProviderError("API 연결 실패 또는 시간 초과") from None



class PooledTransport:
    """Shared HTTP pool only: keys are request headers; budgets/metrics stay per room."""
    def __init__(self, client=None):
        import httpx
        self.client = client or httpx.Client(
            limits=httpx.Limits(max_connections=8, max_keepalive_connections=8),
            follow_redirects=False)

    def __call__(self, url, body, headers, timeout):
        import httpx
        try:
            response = self.client.post(url, json=body, headers=headers, timeout=timeout)
            try:
                code = response.status_code
                if code in (408, 429) or 500 <= code < 600:
                    raise TransientProviderError(f'API HTTP {code}: 일시적인 요청 실패', retry_after_seconds(response.headers.get('Retry-After')))
                if not 200 <= code < 300:
                    raise ProviderError(f'API HTTP {code}: 키·모델·요청 설정을 확인하세요')
                return response.json()
            finally:
                response.close()
        except httpx.RequestError:
            raise TransientProviderError('API 연결 실패 또는 시간 초과') from None

    def close(self):
        self.client.close()


class CloudClient:
    def __init__(self, provider, model, *, timeout=60, max_output_tokens=1024, transport=post_json):
        if provider not in ("openai", "gemini") or not model:
            raise ValueError("외부 API는 provider와 명시적인 model이 필요합니다")
        if max_output_tokens < 1:
            raise ValueError("출력 토큰 한도는 양수여야 합니다")
        self.provider, self.model = provider, model
        self.timeout, self.max_output_tokens, self.transport = timeout, max_output_tokens, transport
        self.metrics = []

    def check(self):
        name = "OPENAI_API_KEY" if self.provider == "openai" else "GEMINI_API_KEY"
        if not os.environ.get(name, "").strip():
            raise ProviderError(f"{name} 환경변수가 필요합니다")

    def generate(self, view, schema, feedback=None):
        self.check()
        messages = prompt_messages(view, feedback)
        prompt_bytes=len(json.dumps(messages,ensure_ascii=False).encode())
        try:limit=int(os.environ.get('GAME_MAX_PROMPT_BYTES','262144'))
        except ValueError:raise ProviderError('GAME_MAX_PROMPT_BYTES는 양의 정수여야 합니다') from None
        if limit<1:raise ProviderError('GAME_MAX_PROMPT_BYTES는 양의 정수여야 합니다')
        if prompt_bytes>limit:raise ProviderError('모델 입력 크기 제한을 초과했습니다. 기록 크기와 GAME_MAX_PROMPT_BYTES를 확인하세요')
        schema = wire_schema(schema)
        if self.provider == "openai":
            url = "https://api.openai.com/v1/responses"
            headers = {"Authorization": "Bearer " + os.environ["OPENAI_API_KEY"]}
            body = {"model": self.model, "input": messages, "store": False,
                    "max_output_tokens": self.max_output_tokens,
                    "text": {"format": {"type": "json_schema", "name": "game_action", "strict": True, "schema": schema}}}
        else:
            url = "https://generativelanguage.googleapis.com/v1beta/models/" + quote(self.model, safe="") + ":generateContent"
            headers = {"x-goog-api-key": os.environ["GEMINI_API_KEY"]}
            body = {"systemInstruction": {"parts": [{"text": messages[0]["content"]}]},
                    "contents": [{"role": "user", "parts": [{"text": messages[1]["content"]}]}],
                    "generationConfig": {"maxOutputTokens": self.max_output_tokens,
                        "responseFormat": {"text": {"mimeType": "APPLICATION_JSON", "schema": schema}}}}
        metric = {"provider": self.provider, "model": self.model, "actor": view["self"]["id"],
                  "phase": view["phase"], "input_tokens": None, "output_tokens": None,
                  "total_tokens": None, "success": False,"prompt_bytes":prompt_bytes}
        start = time.perf_counter()
        try:
            data = self.transport(url, body, headers, remaining(self.timeout))
            remaining()
            if self.provider == "openai":
                usage = data.get("usage") or {}
                metric.update(input_tokens=usage.get("input_tokens"), output_tokens=usage.get("output_tokens"), total_tokens=usage.get("total_tokens"))
                if data.get("status") != "completed":
                    raise ValueError("OpenAI 응답이 완료되지 않았습니다")
                parts = [p for item in data.get("output", []) if item.get("type") == "message" for p in item.get("content", [])]
                if any(p.get("type") == "refusal" for p in parts):
                    raise ValueError("OpenAI가 응답을 거절했습니다")
                text = "".join(p["text"] for p in parts if p.get("type") == "output_text")
            else:
                usage = data.get("usageMetadata") or {}
                metric.update(input_tokens=usage.get("promptTokenCount"), output_tokens=usage.get("candidatesTokenCount"), total_tokens=usage.get("totalTokenCount"))
                metric["thinking_tokens"] = usage.get("thoughtsTokenCount")
                candidates = data.get("candidates", [])
                if not candidates or candidates[0].get("finishReason") != "STOP":
                    raise ValueError("Gemini 응답이 차단되었거나 완료되지 않았습니다")
                text = "".join(p.get("text", "") for p in candidates[0].get("content", {}).get("parts", []) if not p.get("thought"))
            result = json.loads(text)
            validate(result, schema)
            metric["success"] = True
            return result
        finally:
            metric["seconds"] = round(time.perf_counter() - start, 3)
            self.metrics.append(metric)


class BudgetClient:
    """한 게임의 재시도를 포함한 실제 생성 시도 횟수를 제한한다."""
    def __init__(self, client: ModelClient, max_calls=200):
        if max_calls < 1:
            raise ValueError("호출 한도는 양수여야 합니다")
        self.client, self.max_calls, self.calls = client, max_calls, 0
        self.metrics = client.metrics

    def check(self):
        self.client.check()

    def generate(self, view, schema, feedback=None):
        if self.calls >= self.max_calls:
            raise CallLimitExceeded(f"설정한 모델 호출 한도 {self.max_calls}회에 도달했습니다")
        self.calls += 1
        return self.client.generate(view, schema, feedback)


class RoutedClient:
    """Explicit model routing within one provider; one budget wraps all routes."""
    def __init__(self, default, fast=None, quality=None):
        self.default=default;self.fast=fast or default;self.quality=quality or default
        self.provider=default.provider;self.model=default.model;self.metrics=[]

    def check(self):self.default.check()

    def generate(self,view,schema,feedback=None):
        route='quality' if feedback or view['phase'] in ('discussion','council') else 'fast'
        client=self.quality if route=='quality' else self.fast
        try:return client.generate(view,schema,feedback)
        finally:
            for metric in client.metrics:metric['model_route']=route
            self.metrics.extend(client.metrics);client.metrics.clear()


def create_client(provider="ollama", model=None, *, base_url=None, max_calls=200, max_output_tokens=1024, transport=None, fast_model=None, quality_model=None):
    if provider == "ollama":
        client = OllamaClient(model or "qwen3:4b-instruct", base_url or "http://127.0.0.1:11434")
    else:
        if base_url:
            raise ValueError("외부 API에는 --base-url을 지정할 수 없습니다")
        client = CloudClient(provider, model, max_output_tokens=max_output_tokens, transport=transport or post_json)
    if provider != 'ollama' and (fast_model or quality_model):
        def route_model(name):
            return CloudClient(provider,name,max_output_tokens=max_output_tokens,transport=transport or post_json) if name else None
        client=RoutedClient(client,route_model(fast_model),route_model(quality_model))
    return BudgetClient(client, max_calls)
