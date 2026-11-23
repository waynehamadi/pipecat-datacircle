# pipecat-datacircle

Find the best GTM APIs. We benchmark all the GTM APIs and we put the results on our website.

Our website: https://datacircle.dev

This package gives [Pipecat](https://github.com/pipecat-ai/pipecat) four function tools that call Datacircle's public API, https://api.datacircle.dev/v1. You need no key and no account.

| Tool | Gives | Call |
| --- | --- | --- |
| `datacircle_list_apis` | Every GTM API on Datacircle, with the API's slug, number of plans, number of providers and best plan | `GET /apis` |
| `datacircle_best_plans` | One API's plans, sorted by score out of 100, best first. Each plan comes with a rank and a price per 1,000 calls | `GET /apis/{slug}/best` |
| `datacircle_search_plans` | Plans that match a provider or plan name, on all APIs or on one API. You can sort them by score, provider or price | `GET /plans` |
| `datacircle_provider` | The APIs a provider sells, and the provider's rank on each API | `GET /providers/{slug}` |

## Install

```bash
pip install pipecat-datacircle
```

The package needs Pipecat 1.4 or later (`pipecat-ai`) and Python 3.11 or later.

## Give the tools to your bot

```python
import os

from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import LLMContextAggregatorPair
from pipecat.services.openai.llm import OpenAILLMService
from pipecat_datacircle import datacircle_tools_schema

llm = OpenAILLMService(api_key=os.environ["OPENAI_API_KEY"])
context = LLMContext(
    [{"role": "user", "content": "Which email finder API is best for under $5 per 1,000?"}],
    tools=datacircle_tools_schema(),
)
user_aggregator, assistant_aggregator = LLMContextAggregatorPair(context)
```

Put `user_aggregator`, `llm` and `assistant_aggregator` in your pipeline as usual. You don't call `register_function`. The LLM service runs each tool with the handler in the tool's schema. Pipecat turns the schemas into each provider's tools. Pipecat's other LLM services take the same tools.

To add the tools beside your own, list them: `LLMContext(messages, tools=[my_tool, *datacircle_functions()])`.

To give only some of the tools, list their names: `datacircle_tools_schema(["datacircle_best_plans"])`.

## Call a tool yourself

`run` returns a tool's output for the model:

```python
from pipecat_datacircle import run

print(run("datacircle_best_plans", {"slug": "email-finder", "limit": 3}))
```

## Tool output for the model

Each result is our API's answer as a dict. Pipecat gives the model this dict as JSON. Each plan comes with a rank, provider, plan, score, price per 1,000 (`perThousand`, in dollars) and a page on datacircle.dev. That page lists every field.

You can give a provider's name in place of a slug. The tool turns `Bright Data` into `bright-data`.

The model gets `{"error": ...}` with the API's error message when the model passes an unknown slug. The error for an unknown API slug lists every valid slug. The model can then pick one and call the tool again. The tool checks each number from the model before the API call. The model gets an error message for any number out of range. The tool doesn't call the API for that number.

## Network

Each tool call makes one HTTPS GET request to https://api.datacircle.dev/v1 and no other network calls. The request runs in a thread, so your pipeline keeps running. The request holds only the tool's own arguments. The API takes up to 60 calls a minute from one IP address. If the API says to wait, the tool waits up to a minute. The tool waits only once per call. All plans are in one file at https://datacircle.dev/datacircle.json.

## Links

- Datacircle: https://datacircle.dev
- The API: https://datacircle.dev/api
- The API's spec: https://api.datacircle.dev/v1/openapi.json
- What each key means: https://datacircle.dev/fields

MIT license.
