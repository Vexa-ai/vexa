/** A workspace page renders with NOTHING turned into code (car 38, CSP `'unsafe-eval'` removed).
 *
 *  The browser's Content-Security-Policy no longer allows `eval`, `new Function` or an async
 *  function's constructor, so the renderer must not reach for them. These tests make every one of
 *  those throw — as the browser would — and render real pages: the components, the literal
 *  attribute values, GFM tables and fences must all still draw, with no fallback notice. */
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { MdxDoc, renderDocTree } from "../MdxDoc";

afterEach(cleanup);

const refused = () => { throw new EvalError("refused by the page's policy: 'unsafe-eval' is not allowed"); };
const AsyncFunction = Object.getPrototypeOf(async function () { /* */ }).constructor as FunctionConstructor;
const GeneratorFunction = Object.getPrototypeOf(function* () { /* */ }).constructor as FunctionConstructor;
const saved: Array<() => void> = [];
function trap(target: object, key: string) {
  const before = Object.getOwnPropertyDescriptor(target, key);
  Object.defineProperty(target, key, { value: refused, configurable: true, writable: true });
  saved.push(() => { if (before) Object.defineProperty(target, key, before); else delete (target as Record<string, unknown>)[key]; });
}

const FunctionPrototype = Function.prototype;
beforeEach(() => {
  // what a page under `script-src` without 'unsafe-eval' refuses
  trap(FunctionPrototype, "constructor");
  trap(AsyncFunction.prototype, "constructor");
  trap(GeneratorFunction.prototype, "constructor");
  trap(globalThis, "eval");
  // `new Function(…)` and `Function(…)` throw; `x instanceof Function` still answers
  const RealFunction = Function;
  const before = Object.getOwnPropertyDescriptor(globalThis, "Function");
  Object.defineProperty(globalThis, "Function", { configurable: true, writable: true,
    value: new Proxy(RealFunction, { apply: refused, construct: refused }) });
  saved.push(() => { if (before) Object.defineProperty(globalThis, "Function", before); });
});
afterEach(() => { while (saved.length) saved.pop()!(); });

const PAGE = `# Plan

<Note>A note.</Note>

<CardGroup cols={2}>
  <Card title="Alpha" href="alpha.md">About alpha.</Card>
  <Card title="Beta" />
</CardGroup>

| Left | Right |
|:-----|------:|
| a    | b     |

\`\`\`bash
echo hi
\`\`\`
`;

describe("a workspace page under a policy with no 'unsafe-eval'", () => {
  it("the trap is real: eval and every function constructor throw", () => {
    expect(() => (globalThis as unknown as { eval: (s: string) => unknown }).eval("1")).toThrow(EvalError);
    expect(() => (AsyncFunction.prototype.constructor as unknown as (s: string) => unknown)("return 1")).toThrow(EvalError);
  });

  it("renders its components, tables and fences with no fallback", () => {
    const { container } = render(<MdxDoc>{PAGE}</MdxDoc>);
    expect(screen.queryByText(/simplified rendering/)).toBeNull();
    expect(screen.getByText("A note.")).toBeTruthy();
    expect(screen.getByText("Alpha")).toBeTruthy();
    expect(screen.getByText("About alpha.")).toBeTruthy();
    expect(container.querySelector("table")).toBeTruthy();
    expect(container.querySelector("pre")?.textContent).toContain("echo hi");
  });

  it("still refuses an expression: the page falls back to plain Markdown, it does not run", () => {
    render(<MdxDoc>{"Hello {globalThis.alert(1)}"}</MdxDoc>);
    expect(screen.getByText(/simplified rendering/)).toBeTruthy();
  });

  it("reads a literal attribute value and nothing else out of the tree", () => {
    expect(() => renderDocTree("<Card title={1} />")).not.toThrow();
    expect(() => renderDocTree("<Card title={window.name} />")).toThrow();
    expect(() => renderDocTree("<NotARegisteredThing />")).toThrow();
  });
});
