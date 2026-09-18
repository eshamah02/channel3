# Channel3 — Product Extraction

This project takes the raw HTML of a product page from any store and pulls out a clean,
structured product: name, price, images, description, sizes and colours, and a category.
It then serves those products over a small API and displays them in a simple React
storefront.

The main idea behind it is short enough to say in one sentence: **most product pages
already describe themselves in a format built for machines, so read that first and only
use the AI for the parts that genuinely need judgement.**

**NOTE**
I used AI to help me with a majority of the development in this project. Despite that, what took the longest was the research I did on what the best way to implement an extraction pipeline is and how we should go about it for this specific use case. 

---

## 1. Running it locally

You need Python 3.12, Node 20 or newer, [uv](https://docs.astral.sh/uv/getting-started/installation/)
for Python dependencies, and an OpenRouter API key.

### Install

```bash
cd channel3
uv sync
```

`uv sync` creates the virtual environment and installs everything listed in
`pyproject.toml`. You don't need to activate anything; `uv run` handles that.

### Add your API key

```bash
echo 'OPEN_ROUTER_API_KEY=sk-or-v1-your-key-here' > .env
```

The name has to be exactly `OPEN_ROUTER_API_KEY`, because that's what `ai.py` looks for.

### Extract the products

```bash
uv run python main.py
```

This reads every HTML file in `data/`, extracts one product from each, and saves the
results as JSON files in `out/products/`. It costs about half a cent in total and prints
a summary when it's done.

Running it again is free. Each file gets fingerprinted, so if the HTML hasn't changed,
it gets skipped without calling the AI at all:

Other things you can do:

```bash
uv run python main.py path/to/page.html    # just one file
uv run python main.py --force              # re-extract everything anyway
uv run python main.py --escalate           # retry weak pages with a smarter model
uv run python main.py --dump-candidates    # see what we found before the AI runs
uv run python main.py --dump-images        # see the images we found
uv run python main.py --dump-semantic      # see how much we shrank each page
```

The three `--dump-` options don't call the AI, so they're free and they're the quickest
way to understand what the code is actually doing.

### Start the API

```bash
uv run uvicorn server.main:app --reload
```

That runs on `http://127.0.0.1:8000`. You can browse and try out every endpoint at
**<http://127.0.0.1:8000/docs>**, which FastAPI generates automatically from the code.

Extraction has to run first, otherwise there's nothing to serve. If you haven't run it,
`/products` returns an empty list rather than an error, and the frontend grid will just
look empty.

### Start the frontend

In a second terminal:

```bash
cd frontend
npm install
npm run dev
```

That opens on `http://localhost:5173`. Keep the API running in the other terminal. Vite
forwards anything starting with `/api` to the API for you, so the browser only ever
talks to one address and there's no CORS setup to worry about.

### Tests

```bash
uv run pytest                    # 412 tests, no API key needed
cd frontend && npm test          # 65 tests
cd frontend && npm run build     # type-check and build
```

The Python tests never touch the network. There's a fake AI client that returns
scripted answers, so the whole pipeline can be tested for free.

---

## 2. How it's put together

Product pages look messy but mostly aren't, because shops want Google and Facebook to read
them. They publish the same facts in formats built for machines: JSON-LD, microdata,
OpenGraph meta tags, and the JSON blob a JavaScript site uses to build itself. A page
usually states its own price four or five times before a human ever sees it. So instead of
sending 300–800 KB of HTML to an expensive model and hoping it finds the price, six small
modules read those machine-readable sources first and collect everything they find as
*candidates*, each tagged with how trustworthy its source is.

That means the AI never sees a web page. It sees a short list of competing claims and picks
between them, which is a job a very cheap model does reliably, and it's what keeps the cost
at roughly a tenth of a cent per product. Images are collected separately and never go near
the model at all. The category is chosen by walking Google's taxonomy one level at a time,
so an invalid category is impossible rather than merely rejected. Every product records
which source won for each field, so you can tell a price the shop published from a price we
read off the page. The results land in `out/products/`, get served by a small FastAPI app,
and are displayed by a React frontend.

**[SYSTEM.md](SYSTEM.md) has the full explanation** — the extraction layers, the standards
they're based on, what each file does, the backend and AI design decisions, and the
measured costs.

---

## 3. System design

The actual extraction pipeline that we've developed would scale with no issues because there's no shared state or any cross page dependency. If we move this pipeline directly into a queue for workers to run on millions of HTML files, it would not need any redesign for it to work. We create hashes on the content and use that to check for duplicates or changes in a file, and this would also be scalable for millions of sites compared to just comparing on the hash of a URL. The way we measure provenance by having tiers A-E is also scalable and would allow us to set alerts on lower tier facts rather than having to wait for a customer complaint to investigate the issue. The cost architecture also holds quite well at scale. In **[SYSTEM.md](SYSTEM.md)**, we go through how we calculated the cost of each model call per product, and we get a maximum cost of approximately $1,000 per million products. For 50 million products, this would cost around $50,000 for a full extraction. This is quite affordable compared to other pipeline setups where we pass in the full chunk of the HTML into a more expensive model for it to parse. 

One major part of this project that absolutely does not scale is the storage/database layer. If we had 50 million products, we certainly would not want to store them in individual JSON files that are costly to read and parse. Rather, we would want these products sotred in a database, where we could index on id or even paginate on the endpoint (which today, returns everything). Because we don't have a queue or a pub/sub system set up, and because ingestion is a single process, there is no way to implement retries, backoffs, dead letter queues, or partial completion scenarios. This means that our system has no memory of when it starts or stops the ingestion process unless it's fully complete. This would not scale. One other major thing that we don't account for, is for a product page that is purely client-side rendered. Our system does not account for that at all, and our ingestion process only works for server rendered content. Also, because we're only in development phase, we have the HTML files given to us nicely in a /data folder. At 50 million products, we would have to develop a script of some sort that curl's webpages and puts the response into these files for us to parse.

For the frontend, we would want to be able to provide information like "does this product come in a large?". This would require us to provide an API that goes through the axis list, rather than a traversal over the combinations. The provenance metadata should likely also be provided to an agent. They should know how confidently to recite their responses to a user and how they should phrase a response that came from a lower tier versus a higher tier. We would also want to implement a batch endpoint so an agent can hydrate a comparison through one endpoint. We would want some sort of webhook or 2-way street so that agents do not have to poll continuously for the products the users have saved or are looking at. It would also be cool to develop some sort of a sandbox environment where the extraction pipeline was completely exposed so a developer could point to any URL and see the candidate bundle.
