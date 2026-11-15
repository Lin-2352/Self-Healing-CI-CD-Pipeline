## Background

This chapter explains the technology the rest of the report assumes. Readers who already use GitHub Actions and llama.cpp can skip it.

### Continuous integration

Continuous integration (CI) means that every change pushed to a shared repository is built and tested automatically on a clean machine. The point is to find out within minutes, and not at release time, that a change broke something. Continuous delivery goes one step further and ships every change that passes. This project only deals with the first half: a change fails CI, and we want to know why.

### GitHub Actions

GitHub Actions is the CI service built into GitHub. A **workflow** is a YAML file under `.github/workflows/`. It names the events that start it (a push, a pull request) and holds one or more **jobs**. Each job runs on a fresh **runner**, a virtual machine such as `ubuntu-latest`, and is a list of **steps**: either a shell command (`run:`) or a reusable action (`uses:`). One execution of a workflow is a **run**. A job that needs another job to finish first declares it with `needs:`, and an `if:` condition decides whether a job runs at all; `failure()` is true when an earlier job failed.

A step that exits with a non-zero status fails its job, and GitHub keeps the full text log of every step. The log is what a human reads to find the cause, and it is also what this project hands to the model. The job-log endpoint of the GitHub API answers with a redirect to a signed download address on a different host, which matters for how credentials are sent (Chapter 6).

Our demo workflow is `.github/workflows/demo-ci.yml`. Chapter 5 walks through it.

### Webhooks

A webhook is an HTTP request that one system sends to another when something happens, instead of the second system asking repeatedly. GitHub can send one for a finished workflow run. Our notify job sends its own, from inside the workflow, so that it can add fields GitHub does not include, such as whether the failure was a code error. A receiver should authenticate such a request; ours checks a shared secret in a header.

### n8n

n8n is a workflow automation tool. A workflow is a graph of nodes: a trigger node (here a webhook), HTTP request nodes, nodes that run JavaScript, and conditional nodes. Each node receives items from the previous one and passes items on. A node can be set to stop the whole run when it fails or to continue and hand the error on as data; the workflow relies on both behaviours. We used n8n 1.x. Version 2.x did not register our webhook (docs/guides/SETUP.md).

### Serving a language model

**llama.cpp** is a C and C++ program that runs language models from a single file in the GGUF format. Its `llama-server` exposes an HTTP API that follows the OpenAI chat-completions format, so any client written for that API can talk to it. The pieces that matter here:

- **Quantization.** The weights are stored with fewer bits than the 16 used in training. The model we use is `UD-Q4_K_XL` from the unsloth repository: roughly 4-bit weights, 17.6 GB on disk (kaggle/endpoint/models.json). Smaller files fit in GPU memory and read faster, at some cost in accuracy.
- **Tensor split.** With `-sm tensor` the work of each layer is divided over both GPUs, so they compute at the same time. Without it, the layers are divided between the GPUs and only one works at a time.
- **Multi-token prediction (MTP).** The model has a small extra head that guesses the next few tokens, and the full model checks the guesses in one pass. When the guesses are right, several tokens cost one step. The server reports how often they were accepted.
- **Streaming.** The reply can arrive as a stream of small events (server-sent events), so the client sees the first tokens at once instead of waiting for the whole answer.
- **Thinking mode.** Qwen3.8 can write out its reasoning before the answer. That usually gives better answers and always takes longer. A request can switch it on or off with `enable_thinking`.

### Kaggle and the tunnel

Kaggle offers notebooks with a choice of GPUs, including an accelerator named two T4s (2 x 16 GB). A notebook run is called a session, and there is no inbound network access to it. To reach the model from outside, the notebook starts `cloudflared` in "quick tunnel" mode, which makes the local port available at a random `https://<words>.trycloudflare.com` address. Two properties of this service drive design decisions later. The address is new on every start. And the tunnel closes a connection whose origin sends nothing for 120 seconds, which is why the workflow streams the model reply.

### SEARCH/REPLACE edits

Asking a model to return a whole corrected file wastes tokens and invites accidental changes elsewhere in the file. We ask instead for edits in a fixed block format:

```text
<<<<<<< SEARCH
(lines copied from the current file)
=======
(the replacement lines)
>>>>>>> REPLACE
```

The SEARCH text must occur exactly once in the file. The workflow applies the edit by plain text replacement and rejects the reply if the text is absent or ambiguous. Chapter 9 compares this with other formats.
