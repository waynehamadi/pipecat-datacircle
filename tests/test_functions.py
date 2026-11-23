"""Offline tests: the tools against a local stand-in for the API on 127.0.0.1, by their own run, by each handler as an
LLM service calls it, as Pipecat's adapters turn them into each provider's tools, and advertised in an LLMContext."""

import asyncio
import importlib
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List
from urllib.parse import parse_qs, urlparse

import pytest

from pipecat_datacircle import (
    HANDLERS,
    TOOL_NAMES,
    DatacircleError,
    datacircle_function,
    datacircle_functions,
    datacircle_tools_schema,
    run,
)
from pipecat_datacircle import _api

try:
    from pipecat.adapters.llm.schemas.function_schema import FunctionSchema
    from pipecat.adapters.llm.schemas.tools_schema import ToolsSchema
except ImportError:
    from pipecat.adapters.schemas.function_schema import FunctionSchema
    from pipecat.adapters.schemas.tools_schema import ToolsSchema

PLAN = {"api": "Employee email", "rank": 1, "provider": "Hunter", "plan": "Starter", "score": 80.1, "perThousand": 4.9,
        "page": "https://datacircle.dev/x", "terms": "long", "aggregators": ["a"]}
SEEN = []
BUSY = []


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        url = urlparse(self.path)
        query = {key: values[0] for key, values in parse_qs(url.query).items()}
        SEEN.append((url.path, query, self.headers.get("User-Agent")))
        headers = {}
        if url.path == "/v1/plans" and query.get("query") == "busy" and not BUSY:
            BUSY.append(1)
            status, body, headers = 429, {"error": "slow down"}, {"Retry-After": "1"}
        elif url.path == "/v1/apis":
            status, body = 200, [{"api": "Employee email", "slug": "email-finder", "plans": 9, "providers": 3, "extra": 1},
                                 {"api": "Employee profile", "slug": "linkedin-profile", "plans": 4, "providers": 2}]
        elif url.path == "/v1/apis/email-finder/best":
            status, body = 200, {"api": "Employee email", "slug": "email-finder", "total": 1, "plans": [PLAN]}
        elif url.path.startswith("/v1/apis/"):
            status, body = 404, {"error": "no API has the slug nope", "slugs": ["email-finder", "linkedin-profile"]}
        elif url.path == "/v1/plans" and query.get("query") == "down":
            status, body = 503, {"error": "down for a minute"}
        elif url.path == "/v1/plans":
            status, body = 200, {"total": 1, "offset": 0, "plans": [PLAN]}
        elif url.path == "/v1/providers/bright-data":
            status, body = 200, {"provider": "Bright Data", "slug": "bright-data", "website": "https://brightdata.com",
                                 "apis": [{"api": "Employee email", "slug": "email-finder", "rank": 7, "sheetPage": "s",
                                           "plans": [dict(PLAN, rank=9, plan="B"), dict(PLAN, rank=7, plan="A")]}]}
        else:
            status, body = 404, {"error": "no provider has this slug"}
        data = json.dumps(body).encode()
        self.send_response(status)
        for key, value in {"Content-Type": "application/json", "Content-Length": str(len(data)), **headers}.items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(data)


@pytest.fixture(scope="module", autouse=True)
def stand_in():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    before = _api.BASE_URL
    _api.BASE_URL = f"http://127.0.0.1:{server.server_address[1]}/v1"
    yield
    _api.BASE_URL = before
    server.shutdown()


class Params:
    """What an LLM service hands a handler, of it the arguments and the result callback."""

    def __init__(self, name: str, arguments: Dict[str, Any]):
        self.function_name, self.tool_call_id, self.arguments = name, "call_1", arguments
        self.results: List[Any] = []

    async def result_callback(self, result: Any, *, properties: Any = None) -> None:
        self.results.append(result)


def handled(name: str, **arguments: Any) -> Any:
    params = Params(name, arguments)
    asyncio.run(HANDLERS[name](params))
    assert len(params.results) == 1
    return params.results[0]


def test_the_schemas():
    schemas = datacircle_functions()
    assert [s.name for s in schemas] == TOOL_NAMES and all(isinstance(s, FunctionSchema) for s in schemas)
    for schema in schemas:
        assert "https://datacircle.dev" in schema.description and schema.handler is HANDLERS[schema.name]
        assert schema.to_default_dict()["parameters"]["type"] == "object"
    assert datacircle_function("datacircle_best_plans").required == ["slug"]
    assert datacircle_function("datacircle_provider").required == ["slug"]
    assert datacircle_function("datacircle_list_apis").required == []
    search = datacircle_function("datacircle_search_plans").properties
    assert search["sort"]["enum"] == ["score", "provider", "price"]
    assert search["limit"] == {"type": "integer", "minimum": 1, "maximum": 50,
                               "description": "How many plans to give, 1 to 50. 10 when left out."}
    search["limit"]["maximum"] = 5  # each schema holds its own copy
    assert datacircle_function("datacircle_search_plans").properties["limit"]["maximum"] == 50
    tools = datacircle_tools_schema(["datacircle_provider", "datacircle_list_apis"])
    assert isinstance(tools, ToolsSchema) and [s.name for s in tools.standard_tools] == ["datacircle_provider", "datacircle_list_apis"]
    assert datacircle_tools_schema([]).standard_tools == []
    with pytest.raises(ValueError, match="its tools are datacircle_list_apis"):
        datacircle_functions(["datacircle_nope"])
    with pytest.raises(TypeError):
        datacircle_functions("datacircle_provider")


def test_each_tool_by_its_handler():
    SEEN.clear()
    assert handled("datacircle_list_apis", query="profile") == \
        {"total": 1, "apis": [{"api": "Employee profile", "slug": "linkedin-profile", "plans": 4, "providers": 2}]}
    assert handled("datacircle_list_apis", limit=1)["apis"][0]["slug"] == "email-finder"
    best = handled("datacircle_best_plans", slug="Email Finder", limit=3)
    assert best["plans"] == [{key: value for key, value in PLAN.items() if key not in ("terms", "aggregators")}]
    found = handled("datacircle_search_plans", query="hunter", sort="price", limit=2)
    assert found["total"] == 1 and "terms" not in found["plans"][0]
    seller = handled("datacircle_provider", slug="Bright Data")
    assert seller["apis"] == [{"api": "Employee email", "slug": "email-finder", "rank": 7, "plans": 2,
                               "best": {"plan": "A", "score": 80.1, "perThousand": 4.9, "page": "https://datacircle.dev/x"}}]
    assert ("/v1/apis/email-finder/best", {"limit": "3"}, _api.USER_AGENT) in SEEN
    assert ("/v1/plans", {"query": "hunter", "sort": "price", "limit": "2"}, _api.USER_AGENT) in SEEN
    assert _api.USER_AGENT.startswith("pipecat-datacircle/")
    assert handled("datacircle_best_plans", slug="email-finder", color="red")["total"] == 1  # an argument it doesn't take
    assert json.loads(json.dumps(seller, ensure_ascii=False)) == seller  # what the aggregator puts in the context


def test_refusals_in_the_apis_words():
    words = handled("datacircle_best_plans", slug="nope")["error"]
    assert "answered 404" in words and "email-finder, linkedin-profile" in words
    assert "no provider has this slug" in handled("datacircle_provider", slug="nobody")["error"]
    assert "answered 503" in handled("datacircle_search_plans", query="down")["error"]
    assert "Give a provider's slug" in handled("datacircle_provider", slug="  ")["error"]
    assert "Give an API's slug" in handled("datacircle_best_plans")["error"]
    with pytest.raises(DatacircleError) as raised:
        _api.get("/plans", {"query": "down"})
    assert raised.value.status == 503 and raised.value.retryable


def test_arguments_out_of_range_never_reach_the_api():
    SEEN.clear()
    assert run("datacircle_search_plans", {"sort": "cheapest"}) == {"error": "sort must be score, provider or price, not 'cheapest'"}
    assert run("datacircle_best_plans", {"slug": "email-finder", "limit": 80}) == {"error": "limit must be from 1 to 50, not 80"}
    assert "offset must be a whole number" in run("datacircle_search_plans", {"offset": 1.5})["error"]
    assert SEEN == []
    assert run("datacircle_best_plans", {"slug": "email-finder", "limit": "2"})["total"] == 1  # a number sent as text
    assert run("datacircle_list_apis", None)["total"] == 2
    with pytest.raises(ValueError):
        run("datacircle_nope", {})


def test_waits_once_on_429():
    BUSY.clear()
    assert handled("datacircle_search_plans", query="busy")["total"] == 1


def _adapter(module: str, name: str):
    for prefix in ("pipecat.adapters.llm.services.", "pipecat.adapters.services."):
        try:
            return getattr(importlib.import_module(prefix + module), name)()
        except ImportError:
            continue
    pytest.skip(f"{name} needs an extra of pipecat-ai")


@pytest.mark.parametrize("module,name", [("open_ai_adapter", "OpenAILLMAdapter"), ("anthropic_adapter", "AnthropicLLMAdapter"),
                                         ("gemini_adapter", "GeminiLLMAdapter"), ("bedrock_adapter", "AWSBedrockLLMAdapter")])
def test_each_providers_tools(module, name):
    tools = _adapter(module, name).to_provider_tools_format(datacircle_tools_schema())
    text = json.dumps(tools, default=str)
    assert all(tool_name in text for tool_name in TOOL_NAMES) and "email-finder" in text


def test_an_llm_context_takes_them():
    from pipecat.processors.aggregators.llm_context import LLMContext

    assert [s.name for s in LLMContext(tools=datacircle_tools_schema()).tools.standard_tools] == TOOL_NAMES
    mine = FunctionSchema(name="what_time", description="The time.", properties={}, required=[])
    listed = LLMContext(tools=[mine, *datacircle_functions()]).tools.standard_tools
    assert [s.name for s in listed] == ["what_time", *TOOL_NAMES] and listed[1].handler is HANDLERS["datacircle_list_apis"]
