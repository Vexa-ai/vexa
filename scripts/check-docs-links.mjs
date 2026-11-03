// The verdict of gate:docs-links on one `mint broken-links` run (see scripts/gates.mjs).
export const MINT_VERSION = "4.2.1018";
export function judgeMintOutput(status, out) {
  const errs = [];
  const parse = out.split("\n").filter((l) => /Unable to parse|Error compiling|Expected a closing tag/i.test(l));
  if (parse.length) errs.push("docs pages that do not compile:", ...parse.map((l) => "   " + l.trim()));
  if (status !== 0) errs.push(`mint broken-links exited ${status}`);
  if (!/no broken links found/.test(out)) errs.push("mint broken-links did not report success:", ...out.trim().split("\n").slice(-15).map((l) => "   " + l));
  return errs;
}
