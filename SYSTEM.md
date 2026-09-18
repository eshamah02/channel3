# How this works

Design notes for the product extraction pipeline. For setup and run instructions, see
[README.md](README.md).

Contents:

1. [How it works](#1-how-it-works)
2. [Where we learned the formats](#2-where-we-learned-the-formats)
3. [What's in the repo](#3-whats-in-the-repo)
4. [The backend](#4-the-backend)
5. [The AI part, and what it costs](#5-the-ai-part-and-what-it-costs)
6. [The frontend](#6-the-frontend)

---

## 1. How it works

### The problem with the obvious approach

The simplest thing you could do is send the whole HTML file to a large AI model and ask
for JSON back. That does work. But a product page is around 300–800 KB of mostly
navigation menus, scripts and styling, so you'd be paying to send roughly 150,000 tokens
of junk, and you'd be trusting the model to correctly read a price out of all that noise.

### Why there's a better way

Product pages aren't really as messy as they look, because shops want to be read by
machines. They want Google to show their price in search results and Facebook to show
their photo when someone shares a link. So they publish the same facts several times over
in formats designed for programs rather than people:

- **JSON-LD** is a block of JSON sitting in a `<script>` tag that says, in plain terms,
  "this page is a product, it's called X, it costs Y." This is the best source there is.
- **Microdata** does the same job using extra attributes sprinkled on normal HTML tags,
  like `itemprop="price"`.
- **Meta tags** like `og:title` and `og:image` are the OpenGraph tags that produce link
  previews on social media.
- **Embedded state** is the blob of JSON that a JavaScript site leaves in the page to
  build itself from.

A page typically states its own price four or five times before a human ever reads it.

### What we actually do

Six small modules each read one of those sources and write down everything they find as
a list of *candidates*. Nothing decides anything yet; they just collect. So the AI never
sees a web page. It sees a short list of competing claims:

```
name          A  "Nike Air Force 1 '07 LV8 Men's Shoes"     <- JSON-LD
name          B  "Nike Air Force 1 LV8 | Nike.com"          <- og:title
price.price   A  76.99                                       <- JSON-LD
price.price   D  "£76.99"                                    <- rendered text
```

Now the model's job is much smaller. It isn't reading a website, it's picking the better
of two names and turning `"£76.99"` into `76.99` plus `GBP`. That's a job a very cheap
model does reliably, and it's the whole reason this is affordable.

### The tiers

The letter next to each candidate records where it came from. Lower letters mean the site
said it more explicitly:

- **A** — JSON-LD or microdata. The shop deliberately published this as product data.
- **B** — meta tags, OpenGraph, the page title. Written for machines but vague.
- **C** — the JavaScript state blob. Precise, but we're guessing at what the key names mean.
- **D** — visible text on the page. Definitely what a shopper sees, needs interpreting.
- **E** — the model made it up from context. No source at all.

This ranking is per field, not global. `og:title` is a good place to get a name and
`og:site_name` is a bad place to get a brand, even though both are OpenGraph.

Every product we save records which tier won for each field. This is a real extract from
one of the sample pages:

```json
"field_tiers": {"name": "A", "price.price": "A", "price.compare_at_price": "D", "description": "E"}
```

That turns out to be one of the more useful things in the project. The current price came
straight from the shop's own product data, so it's a quotation. The original price was
read off the visible text, so it's our interpretation. The description didn't exist
anywhere and the model wrote it. Those three deserve different amounts of trust, and now
you can tell which is which.

### The whole flow

```
raw HTML (300-800 KB)
   |
   |  six layers read the page and collect candidates      free
   v
a few KB of candidates
   |
   |  harvest images and fix them to full resolution       free
   |  ask the AI to pick winners and tidy formats          2 calls
   |  walk Google's category tree one step at a time       2-5 calls
   |  check the answers trace back to something real       free
   v
out/products/<id>.json  ->  API  ->  React storefront
```

---

## 2. Where we learned the formats

None of the layers were guesswork. These are the references we actually used, and the
ones that settle a specific parsing rule are linked in the code next to the rule.

**Structured data**

- [JSON-LD 1.1](https://www.w3.org/TR/json-ld11/) — how `@context` and `@graph` work, and
  why a node's `@type` can be a list rather than a single string.
- [schema.org `Product`](https://schema.org/Product), [`Offer`](https://schema.org/Offer),
  and [`ProductGroup`](https://schema.org/ProductGroup) — the actual vocabulary: `price`,
  `priceCurrency`, `sku`, `availability`, `brand`, `hasVariant`.
- [Google's product structured data docs](https://developers.google.com/search/docs/appearance/structured-data/product)
  — helpful for which of those properties shops really fill in, as opposed to which ones
  exist on paper.
- [HTML Standard: Microdata](https://html.spec.whatwg.org/multipage/microdata.html) and
  [how to read a microdata value](https://html.spec.whatwg.org/multipage/microdata.html#values)
  — the second link matters more than it sounds. Where a value lives depends on the tag:
  `content` on `<meta>`, `href` on `<a>`, `src` on `<img>`, `datetime` on `<time>`, and
  the text otherwise. Getting that table right is most of the microdata layer.
- [The Open Graph protocol](https://ogp.me/) — `og:title`, `og:image`, `og:url`, and the
  `product:` price tags.
- [Twitter card markup](https://developer.twitter.com/en/docs/tweets/optimize-with-cards/overview/markup.html)
  — the `twitter:` tags, which some sites fill in even when they skip OpenGraph.

**Images**

- [HTML Standard: `srcset`](https://html.spec.whatwg.org/multipage/images.html#srcset-attributes)
  — reading the list of widths in a `<picture>` block so we can take the biggest one
  rather than whatever a browser would have chosen.
- The URL conventions Cloudinary, Adobe Scene7, Imgix, Shopify and Contentful use to
  encode image size, which is how we spot a thumbnail and ask for the original instead.
  They're credited in the code, but nothing branches on them: an image host we've never
  seen just passes through unchanged.

**Categories**

- [Google Product Taxonomy](https://www.google.com/basepages/producttype/taxonomy.en-US.txt)
  — the 5,595 category names in `categories.txt`.

---

## 3. What's in the repo

```
models.py            the Product schema plus our Variant and envelope models
ai.py                provided helper, unchanged
config.py            which AI model each step uses
main.py              the command-line tool
categories.txt       Google's 5,595 categories
data/                input HTML
out/products/        one JSON file per extracted product
server/main.py       the API
frontend/            the React app
tests/               412 tests
```

### The `extraction/` folder

It splits three ways: find the facts, choose between them, then check the choice.

**Finding the facts**

- `dom.py` — parses the HTML, and shrinks the page down to just structure and text. This
  is the single biggest cost saving in the project.
- `candidates.py` — the shared vocabulary. Defines what a candidate is, what the tiers
  mean, and the fixed list of field names. Field names are checked on the way in, so a
  typo can't quietly split one field into two half-filled ones.
- `layers/jsonld.py` — tier A. Reads JSON-LD. Also the only layer that recovers a real
  size/colour matrix, `sku`, and stock status, because schema.org is the only one of
  these formats that publishes them.
- `layers/microdata.py` — tier A. Reads `itemprop` attributes. The fallback for sites
  that mark up their HTML instead of shipping JSON-LD.
- `layers/meta.py` — tier B. OpenGraph, Twitter cards, the `<title>`. Small, and often
  the only thing a page gives you.
- `layers/embedded.py` — tier C. Finds a JavaScript framework's data blob by looking at
  the *shape* of the JSON rather than the variable name, so it isn't tied to any one
  framework.
- `layers/text.py` — tier D. Prices, headings and bullet lists from the visible text.
- `layers/pickers.py` — tier D. Reads size and colour options out of the page's own
  dropdowns and swatches. This is how a page that shows a size picker but never declares
  a proper matrix still ends up with variants.
- `images.py` — collects images from every source, then rewrites each URL to full
  resolution. Runs entirely outside the AI.

**Choosing between them**

- `llm.py` — talks to OpenRouter, tracks token usage and cost, and defines the fake
  client the tests use.
- `prompts.py` — the four prompts, kept in their own file so changing one is a one-file
  diff and `pipeline.py` stays readable.
- `pipeline.py` — the orchestrator. Builds the candidates, asks the model, resolves the
  category, checks the result, assembles the final object.
- `category.py` — Google's category tree and the logic that walks down it.
- `derive.py` — works out the list of available options from the variants, and generates
  ids, slugs and hashes.

**Checking the choice**

- `grounding.py` — makes sure what came back traces to something we actually saw. Every
  image URL has to be one we harvested; every text field has to appear in the page.
- `escalation.py` — scores how well a page went, and if it went badly, offers to re-read
  it with a better model and keep only the fields that improved.
- `ingest.py` — the batch runner: skip unchanged files, write results safely, report
  what happened.

---

## 4. The backend

### Layers, strongest source first

All six layers are listed in one place, best source first:

```python
LAYERS = (jsonld, microdata, meta, embedded, text, pickers)
```

They all have the same shape (take a parsed page, add candidates) and none of them knows
the others exist. Adding a seventh source is one new file and one entry in that tuple.

We run all six even when the first one already worked. That's on purpose. Disagreements
between sources are exactly the information the next step needs, and parsing is free
compared to an AI call, so there's nothing to gain by stopping early.

### The AI chooses, it doesn't copy

The model gets the candidate list, not the page. Its job is judgement: pick between
conflicting values and normalise the formatting. Copying text out of HTML is something
the layers already do perfectly, and models are worse at it than a parser.

Three things follow from that.

Images never go near the model. They aren't even in the list of fields we ask it for. We
collect them ourselves and attach them afterwards, so there's no way for the model to
invent, drop or mistype a URL. Asking an AI to copy twenty long CDN URLs exactly is
asking for trouble with nothing to gain.

Descriptions get their own separate call. `description` and `key_features` are the only
fields no standard publishes, which makes them the only ones that need the page's actual
text rather than the candidate list. Keeping them apart keeps the main prompt small.

Categories don't use a prompt like this at all. See "Categories as a walk, not a guess"
below.

### Stopping the model from making things up

Validation catches output that's the wrong *shape*. It doesn't catch output that looks
completely reasonable and is simply false, which is the failure that actually matters.

We hit a real example. On one page the model returned an original price of $200 against a
current price of $170, implying a discount. The page does contain the number 200, in the
sentence *"free standard shipping on orders above: US/CANADA: 200 USD."* The model had
turned a delivery threshold into a sale that never existed.

The fix wasn't to reword the prompt. It was to make the mistake impossible to express: we
now throw away any "original price" that doesn't match a candidate we found *for that
specific field*. The trade-off is that we'll occasionally miss a genuine sale, and we
took that deliberately. Missing a discount is a much smaller problem than inventing one,
especially on something a customer reads.

Anything we throw away gets written down rather than silently dropped:

```json
"warnings": ["dropped compare_at_price 29.95: not above price 29.95"]
```

### Categories as a walk, not a guess

The category has to be one of 5,595 exact strings from Google's taxonomy. If you ask a
model to produce one of those, you get near-misses that fail validation: `"Shoes"`
instead of `"Apparel & Accessories > Shoes"`.

So we don't ask for a string. We show the model the children of one node as a numbered
list and ask for a number:

```
0. Animals & Pet Supplies
1. Apparel & Accessories
2. Arts & Entertainment
...
```

Then we go one level deeper with that node's children, and repeat. An invalid category
isn't rejected, it's *unsayable*, because the only thing the model can return is a
position in a list of real categories. Each step costs about a hundredth of a cent since
the prompt is short and the answer is one number.

One thing we only found by running all five pages. A floor lamp first came back as plain
`"Furniture"`. The walk had behaved sensibly on its own terms: `Furniture`'s 25 children
are all chairs, tables and storage, so it declined to force a bad fit. The mistake was
the very first step, and `Home & Garden > Lighting > Lamps` exists. Since only 21 of
5,595 categories are top-level, stopping at the first level is nearly always a wrong
starting branch rather than a product that truly has no better home. So a first-level
answer now gets one retry with that branch removed, and the deeper answer wins. Costs one
extra call, only on pages that get stuck.

If the walk fails entirely, the page doesn't fail. We substitute a placeholder, record a
warning, and carry on, so one bad page can't kill a whole batch.

### What we save

The provided `Product` schema is left exactly as it was. Everything we wanted to add
lives in a wrapper around it:

```python
class ExtractedProduct(BaseModel):
    id: str                        # hash of the page URL
    slug: str
    source_url: str | None
    source_file: str
    content_hash: str              # hash of the HTML, used to skip unchanged files
    options: list[VariantOption]   # the available sizes/colours, worked out from variants
    product: Product               # untouched
    extraction: ExtractionMetadata # tiers, warnings, cost
```

Three decisions in there are worth explaining.

The id hashes the URL rather than the page contents. Hashing the contents would hand a
product a brand new id every time its price changed, which breaks anything holding a
reference to it. Hashing the URL stays stable across re-runs and needs no central service
handing out ids. We do hash the contents as well, but that sits on its own field doing its
own job, which is telling us whether we can skip the file.

`options` is calculated from the variants rather than asked for separately. If we asked
the model for both the variant list and the list of available sizes, the two could
disagree with each other. Working one out from the other makes that impossible.

Recording tiers is what lets you tell "the page didn't say" apart from "we didn't look."
A few fields, like `video_url`, `sku` and stock status, are wired up all the way through
but empty on all five sample pages, purely because none of those pages publishes them.
Without the tier information you couldn't distinguish that from a broken extractor.

### Re-running is cheap, crashing is safe

Every source file is fingerprinted, so an unchanged file is skipped without an AI call.
That sounds like a small thing but it changed how the project got built: you can re-run
extraction constantly while working on the API or the frontend and pay nothing.

Each product is written as a complete file or not at all, so a crash halfway through
leaves valid files rather than half-written JSON. Pages are processed five at a time,
since the work is almost entirely waiting on the network. If one page fails, it's
recorded and the rest carry on.

### The API

Two endpoints, both read-only, both browsable at <http://127.0.0.1:8000/docs>:

- `GET /products` — a short summary of every product: id, name, brand, price, one image.
- `GET /products/{id}` — the full record, including the derived options the product page
  needs.

There are two response shapes because a grid card only needs a name, a price and one
photo. Sending the full size-and-colour matrix for every card would make that response
several times bigger than the page could use.

The API re-reads the folder on every request. That means a newly extracted product shows
up on refresh with no restart, which makes developing much nicer. It's also the most
obvious thing here that wouldn't survive real scale, and there's a comment saying so
right next to the code.

One security detail is worth explaining, because it's an easy bug to write. The obvious
way to build the detail endpoint is to make a filename out of the id, like
`out/products/<id>.json`. Don't. An id of `../../etc/passwd` climbs out of the folder,
and FastAPI won't stop you, because as far as it's concerned that's just a string. We
block it twice over. First, the id has to be exactly sixteen hex characters, so anything
odd gets a `422` before we look anything up. Second, and this is the part that actually
matters, we search the list of products we've already loaded rather than building a
filename at all. Take the first check away and there's still no file read to hijack. The
pattern is really just a nicer error message.

Failures stay small. One unreadable file costs you one product rather than the whole
catalogue, and a missing output folder means "nothing extracted yet" and returns an empty
list instead of crashing.

There's no authentication, deliberately. It's read-only, bound to localhost, has no
endpoints that change anything, and only serves data taken from public pages. Putting it
on the internet would need auth and rate limiting at a minimum. That's said in the code
and in the instruction docs as well as here.

---

## 5. The AI part, and what it costs

### Which model

Almost everything runs on `gpt-5-nano`, the cheapest model in the provided price list.
That's only possible because of how the work is arranged: the model never has to read a
web page, it picks between a few pre-extracted strings or returns a number. The bigger
`gpt-5-mini` is only used by `--escalate`, for pages that came out badly, and you have to
ask for it.

### The setting that saved the most money

Output tokens cost eight times what input tokens cost. And modern models spend output
tokens *thinking* before they answer, which you also pay for at the output rate. On a
trivial test prompt, that thinking was 98% of everything we were billed for.

So the biggest lever isn't how much HTML you send, it's how much the model thinks. We
measured it, three runs each on the same prompt:

| Setting | Output tokens | Of which thinking | Cost per call |
| --- | --- | --- | --- |
| default | 2,062 | 2,027 (98%) | $0.000828 |
| `effort: "low"` | 276 | 213 (77%) | $0.000113 |

That's **7.3× cheaper**. And there's a second benefit that matters just as much: on
default settings, the same prompt produced 1,106, then 6,001, then 2,062 output tokens
across three runs. You can't budget for that. Low effort is both cheaper and predictable.

### What it actually cost

All five pages, everything included:

| Page | Cost |
| --- | --- |
| A Day's March trousers | $0.000847 |
| Ace Hardware drill | $0.000934 |
| Article floor lamp | $0.000966 |
| Nike sneakers | $0.001159 |
| L.L.Bean henley | $0.001200 |

That averages about **a tenth of a cent per product**, or roughly **$1,000 per million
products**, across four to six model calls each. There's real run-to-run variation, so
it's fairer to call it $0.0008–$0.0012 per product than to quote one number.

For comparison, here's what the alternatives would cost:

| Approach | Per product | Per million |
| --- | --- | --- |
| Cheap model on our candidate list | ~$0.001 | ~$1,000 |
| Same, with 15% of pages escalating | ~$0.0019 | ~$1,900 |
| Better model on the trimmed page, always | ~$0.0095 | ~$9,500 |
| Better model on the raw HTML | ~$0.038 | ~$38,000 |

Roughly **38× between the naive version and this one**. The layers that produce that
saving cost nothing per page, since they're just parsing. At a million products it's the
difference between a $1,000 bill and a $38,000 one.

The page-shrinking helps in the same way. `--dump-semantic` shows it:

```
file                   raw   semantic   ratio
data/adaysmarch    312,178     11,464    3.7%
data/llbean        599,614     45,072    7.5%
data/nike          775,840     84,688   10.9%
mean                                     7.8%
```

Under 8% of the original size, and the candidate list the model actually receives is
smaller again. Cheap model, small prompt, low thinking effort: three savings that
multiply, and none of them costs accuracy, because the accuracy is coming from the
standards rather than from the model.

### A note on the cost numbers

`ai.py` is untouched and its per-call log prints exactly as provided. Our run total is
calculated separately and comes out lower, for two reasons.

First, thinking tokens are already counted inside output tokens. `_log_usage` prices the
output, then adds the thinking on top at the output rate, which roughly doubles a
reasoning model's reported cost. You can see it in the numbers: a call that replied
`"dlrow olleh"`, maybe five tokens, reported 1,106 output tokens with 1,024 of them
thinking, and the 82 left over is about right for the message.

Second, we make four to six calls per product, so a per-*call* "cost per million queries"
figure is roughly 5× off from cost per million *products*.

Both of these are observations, not complaints. `ai.py` is the measurement helper we were
given, and its own instructions point at the approach we took: keep hold of the response
and hand it to `_log_usage`. Their function measures each call, ours adds up the run.
Editing their file to fix arithmetic we can simply avoid repeating seemed like the worse
trade.

---

## 6. The frontend

For the frontend, we used Vite, React, TypeScript and Tailwind, with two routes and a proxy to the API.

`/` is the catalogue grid and `/product/:id` is the product page. Both read straight from
the API. There's no state management library, because with two pages and nothing to save,
a small hook around `fetch` is the entire requirement.

The one genuinely interesting piece is `lib/variants.ts`. When you pick a size, it has to
find the matching variant, update the price, and grey out combinations that don't exist.
Real products are patchy: black might come in small and medium, white in small only. So
"is this combination available" is a lookup against what actually exists, not every
possible pairing. That logic is plain functions with no React in them, which is why it
accounts for 22 of the 65 frontend tests.
