// Mermaid's own parser on each diagram it's handed (crew#922).
//
// The diagrams come on stdin as a JSON list of strings, never as arguments: they're
// the model's text. Each one's result goes to stdout, in order, as
// {ok, error, line}: Mermaid's own message, and the line it names, as GitHub shows it.
// Mermaid needs a DOM to load (its sanitiser), so jsdom gives it one; nothing is drawn.
import { JSDOM } from "jsdom";

const dom = new JSDOM("<!doctype html><html><body></body></html>");
globalThis.window = dom.window;
globalThis.document = dom.window.document;
const { default: mermaid } = await import("mermaid");
mermaid.initialize({ startOnLoad: false });

let input = "";
for await (const chunk of process.stdin) input += chunk;

const results = [];
for (const text of JSON.parse(input)) {
  try {
    await mermaid.parse(text);
    results.push({ ok: true, error: null, line: null });
  } catch (err) {
    const error = String(err?.message ?? err);
    const named = /on line (\d+)/.exec(error);
    results.push({ ok: false, error, line: named ? Number(named[1]) : null });
  }
}
process.stdout.write(JSON.stringify(results));
