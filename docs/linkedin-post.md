I wanted to get more value from the Codex subscription I already pay for—and use it from my own tools.

So I built Codex HTTP Gateway: a self-hosted HTTP interface around the Codex CLI, with a browser console called Codex Online.

Sign in with your ChatGPT account, then submit prompts from your own applications, continue conversations across requests, and retrieve results through a simple JSON API.

It includes:

- Persistent conversations and run history
- Asynchronous tasks, progress polling, and cancellation
- Model and reasoning effort controls
- Remaining usage limits and reset times, when reported by Codex
- A responsive web console

The goal: make better use of your existing subscription allowance in your personal development workflows. Normal account limits still apply, and the running gateway is designed for private access.

Built with Python, FastAPI, and SQLite. Open source under MIT.

Project: [Codex HTTP Gateway](https://github.com/marinelj/codex-http-gateway)

What would you connect it to first?

#Codex #Python #OpenSource #DeveloperTools #AI
