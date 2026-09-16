## Channel3 Take Home Assignment

### Introduction

Thanks for your interest in Channel3! This assignment will allow you to show how you think about software architecture, engineering, and product. It's meant to provide a taste of the kind of work you'll do as part of the Channel3 team. It's also an opportunity for you to see if you would enjoy thinking through these types of problems.

There are 3 portions to this assignment: backend development, frontend development, and a brief system design at the end. Take your time to complete this. There is no hard deadline. Expect to spend a couple of afternoons or a weekend day working on this — the task is large, so try to time-box your work to 4-6 hours total.

AI is a great tool for ideation and software engineering; we use it every day, and so should you. We’ve provided you with an OpenRouter API Key (with a $25 limit) that you should use in both your preferred AI IDE and in code. However, we ask in the system design for you to use your own words; we want to see how you think. Also, we’ll be reviewing your code, so human-written comments and clean code will make it easier for us to understand your thought process.

### Task

**Setup instructions:**

1. Fork the repo https://github.com/channel3-ai/take-home-2026. This has all of the setup you need, and your final submission will be your fork and the write-up.
2. In `.env`, add the `OPEN_ROUTER_API_KEY` that we sent you.
3. In the `/data` directory, you’ll see the HTML files you’re supposed to process. There’s also a README.md that lists the source URLs for the HTML.
4. All other relevant files are in the root directory. Briefly:
   1. `models.py` - this contains the actual schema you’re hydrating in the Backend step. Any other pydantic models you use should be added here for legibility
   2. `ai.py` - we’ve provided a thin wrapper for the OpenAI responses API. This has a method `_log_usage` that prints out the estimated cost of your AI call for 1 and 10 million products. If you choose to use the `AsyncOpenAI` client directly, you should still probably use `_log_usage`
5. Install packages from `pyproject.toml` with your favorite package manager. Run `main.py` to confirm you’re set up.
   1. I use `uv`, so to get up and running, I’d simply have to run `uv sync` followed by `uv run python main.py`
6. There is no server or frontend code. You can set that up however you please. We use FastAPI for our APIs, and React and the shadcn component library for our frontends.

**Backend:**

Given the HTML for 6 product detail pages, hydrate a `Product` schema from each page. Some constraints and considerations:

1. You cannot hard-code site-specific or page-specific logic. Conditionals like `if domain == nike.com` or X-Paths with selectors for attributes will disqualify your solution.
   1. Just as well, page-specific hints in any of your AI prompts will disqualify your solution. Few-shot prompting, in general, is a legitimate technique; few-shot prompting with examples from the data we provided is not.
2. The input to your system is the raw HTML we’ve provided you. There is a README.md in the data directory with the URLs for the PDPs; you should check out each fully rendered page, and observe what information is and isn’t present in the raw HTML. The goal is to hydrate the schema with all of the data visible on the PDP, including all full resolution images.
3. The `Product` schema has a `Category` field. This must be one of the categories from Google’s Product Taxonomy; we have a validator on the pydantic model.
4. The `Product` schema as a `variant` field typed `list[Any]`. You must decide how to structure Variants; a Variant for a product is a discrete configuration/selection of the product, such as size, color, or fit.

We will run your solution on HTML for product pages from other sites. So, we encourage you to make your solution as generalized as possible.

**Frontend:**

After extracting structured `Product` data for products from the various sources, create a small web application that does the following:

1. Shows a structured grid / catalog of the product data, like you’d see when browsing through a brand’s website
2. Clicking a product should open a PDP (product detail page) showing more detailed information on that product (image, title, description, etc).

This **SHOULD**:

- Take in the structured `Product` data you extracted in the backend portion of this assignment
- Have clear organization and details

You **DO NOT** need to include any:

- Authentication
- Database / persistent storage
- Any extra pages (home, about, etc)
- Hosting it on a live site

We leave this part intentionally generic - we want to see how you’d lay out a UI, transition between pages, take attention to details, etc. Please focus on making these two pages polished, something you’d feel comfortable shipping to customers, and do not spend time on any pages or features we didn’t mention here.

**System Design:**

Provide a brief (1-2 paragraph) write-up of how you would turn the system you’ve built into a web-scale operation. For the backend, how would you scale this system up from 5 products to 50 million? What assumptions have you made here that will scale, and what assumptions won’t scale? For the frontend, what API would you provide to power agentic shopping apps? What other tools can you provide developers to help power new shopping experiences?

### Submission Instructions

1. Push up your fork to a public GitHub branch (or, you can make it protected and choose to share it with our GitHub accounts).
2. Update the README.md at the root directory with instructions for running both your backend ingestion and your server + FE.
3. Include your system design in the README.md, either written in the markdown or as a link to your preferred word processor.
