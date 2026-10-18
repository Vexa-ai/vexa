"use client";
/** A PLAIN PAGE'S HEAD (founder, live app.dev on the `finos` page: "it does not say now what its
 *  path is", and the page started with its first paragraph — no title). The title as the page's
 *  heading, and under it one quiet line saying where the page lives: the workspace's name, then
 *  each folder, every segment opening that folder in Files. No icon band; on a narrow panel the
 *  line truncates from the left, so the folder nearest the page stays readable. */
import { WorkspaceName } from "../ui-kit/WsLink";
import { splitLeadingH1 } from "./workspaceFrontPage";

const humanize = (file: string) => {
  const base = file.replace(/\.(md|mdx|markdown)$/i, "").replace(/[-_]+/g, " ").trim();
  return base ? base[0].toUpperCase() + base.slice(1) : file;
};

/** The page's title and the body without its leading `# Title`: the H1, else the frontmatter
 *  `title:`, else the file name made readable. */
export function pageTitle(body: string, path: string): { title: string; body: string } {
  const front = splitLeadingH1(body);
  if (front.title) return { title: front.title, body: front.body };
  const fm = /^---\n([\s\S]*?)\n---/.exec(body ?? "")?.[1] ?? "";
  const t = /^title:\s*["']?(.+?)["']?\s*$/m.exec(fm)?.[1];
  return { title: t || humanize(path.split("/").pop() || path), body };
}

/** WHERE A PAGE LIVES, as one quiet line: the workspace's name, then each folder — every segment
 *  opening that folder in Files — and, with `file`, the file's own name last (not a link: it is the
 *  page in front). A plain page draws it under its title; a workspace README draws it with the file
 *  (founder 2026-10-15: a desktop tile opens the README "as a page … with its path line"). */
export function PagePath({ path, slug, onFolder, reads, file }: {
  path: string; slug?: string; onFolder: (dir: string) => void; reads?: string[]; file?: boolean;
}) {
  const parts = path.split("/").filter(Boolean);
  const dirs = parts.slice(0, -1);
  const ws = slug || "personal";
  const outside = !!reads && reads.length > 0 && !reads.includes(ws);
  return (
    <nav className="vx-pagehead-path" aria-label="Where this page lives" data-page-path="">
      <span className="vx-pagehead-trail">
        <button type="button" className="vx-pagehead-seg" data-path-seg="" onClick={() => onFolder("")}>
          {slug ? <WorkspaceName slug={slug} /> : "Personal"}
        </button>
        {dirs.map((d, i) => (
          <span key={i} className="vx-pagehead-step">
            <span aria-hidden className="vx-pagehead-sep">›</span>
            <button type="button" className="vx-pagehead-seg" data-path-seg={dirs.slice(0, i + 1).join("/")}
              onClick={() => onFolder(dirs.slice(0, i + 1).join("/"))}>{d}</button>
          </span>
        ))}
        {file && parts.length > 0 && <span className="vx-pagehead-step" data-path-file="">
          <span aria-hidden className="vx-pagehead-sep">›</span>
          <span className="vx-pagehead-file">{parts[parts.length - 1]}</span>
        </span>}
        {outside && <span className="vx-pagehead-scope" data-outside-scope=""> · not read by this chat</span>}
      </span>
    </nav>
  );
}

export function PageHead({ title, path, slug, onFolder, reads }: {
  title: string; path: string; slug?: string;
  /** the chat's read set: a page from a workspace outside it says so, quietly (founder, live: a
   *  "Mentioned in" page in his switched-off Personal opened to nothing) */
  reads?: string[];
  /** open Files at this folder ("" = the workspace's root) */
  onFolder: (dir: string) => void;
}) {
  return (
    <header className="vx-pagehead" data-page-head="">
      <h1 className="vx-pagehead-title" data-doc-title="">{title}</h1>
      <PagePath path={path} slug={slug} onFolder={onFolder} reads={reads} />
    </header>
  );
}
