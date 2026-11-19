/** `style-src-elem` admits a `<style>` only with the response's nonce. The layout's first script
 *  stamps it on every `<style>` a running script creates; markup is never stamped. Mermaid's one
 *  scoped `<style>`, inserted as SVG text, carries it too, and no other. */
import { describe, expect, it } from "vitest";
import { styleNonceBootstrap } from "../contentSecurityPolicy";
import { withStyleNonce } from "../../ui-kit/docDiagrams";

describe("the style nonce", () => {
  it("a <style> a script creates carries the nonce; one parsed from markup does not", () => {
    const realCreate = Document.prototype.createElement, realCreateNS = Document.prototype.createElementNS;
    try {
      // the layout runs this text as an inline script; here it runs the same way
      // eslint-disable-next-line no-new-func
      new Function(styleNonceBootstrap("n0nce+/="))();
      expect(document.createElement("style").getAttribute("nonce")).toBe("n0nce+/=");
      expect(document.createElementNS("http://www.w3.org/2000/svg", "style").getAttribute("nonce")).toBe("n0nce+/=");
      expect(document.createElement("div").hasAttribute("nonce")).toBe(false);
      const box = document.createElement("div");
      box.innerHTML = "<style>body{}</style>";
      expect(box.querySelector("style")!.hasAttribute("nonce")).toBe(false);
    } finally {
      Document.prototype.createElement = realCreate;
      Document.prototype.createElementNS = realCreateNS;
    }
  });

  it("the nonce is written as data, never as script", () => {
    expect(styleNonceBootstrap(`a");alert(1);("`)).toContain(JSON.stringify(`a");alert(1);("`));
  });

  it("mermaid's own <style> gets the nonce; a <style> inside a label does not", () => {
    const svg = `<svg id="m1" viewBox="0 0 1 1"><style>#m1{fill:red}</style><g><text>&lt;style&gt;</text><style>x{}</style></g></svg>`;
    const out = withStyleNonce(svg, "abc=");
    expect(out.startsWith(`<svg id="m1" viewBox="0 0 1 1"><style nonce="abc=">`)).toBe(true);
    expect(out.match(/nonce=/g)).toHaveLength(1);
    expect(withStyleNonce(svg, `x" onload="y`)).toBe(svg);
    expect(withStyleNonce(svg, "")).toBe(svg);
  });

  it("mermaid's <style>, already stamped and serialized with its nonce hidden, gets the nonce back", () => {
    // what mermaid returns in a browser: the bootstrap stamped the element it created, the browser
    // hid the value once it was in the document, and the serialized text keeps an empty attribute
    const svg = `<svg id="m2" viewBox="0 0 1 1"><style nonce="">#m2{fill:red}</style><g><style nonce="">x{}</style></g></svg>`;
    const out = withStyleNonce(svg, "abc=");
    expect(out.startsWith(`<svg id="m2" viewBox="0 0 1 1"><style nonce="abc=">#m2{fill:red}</style>`)).toBe(true);
    expect(out.match(/nonce="abc="/g)).toHaveLength(1);
    expect(out).toContain(`<g><style nonce="">x{}</style></g>`);
  });
});
