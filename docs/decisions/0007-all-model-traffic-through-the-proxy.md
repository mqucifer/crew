# 7. All model traffic goes through the proxy

- **Date:** 2026-09-23
- **Status:** Accepted

## Context

`crew doctor` was run straight at SGLang on the Spark because it sent no key
and got a 401 from the proxy. A direct probe can pass while the path every
tick uses is broken. The Sponsor: "I want to make sure we're going to do
everything through the proxy going forward."

## Decision

Every model call, including `crew doctor` and any ad-hoc probe, goes through
the LiteLLM proxy (`CREW_LLM_BASE_URL`, :4000). Nothing calls or probes SGLang
on the Spark directly.

## Consequences

- To find which layer failed, read the proxy's logs and SGLang's start log,
  not a direct request.
- Starting and stopping SGLang on the Spark is serving, not traffic, so it's
  allowed.
