import { readFileSync, readdirSync } from "node:fs";
import { describe, expect, it } from "vitest";

/** GHSA-qwww-vcr4-c8h2 — react-router 7.12.0–8.2.0, high.
 *
 * "RSC Mode CSRF Bypass Allows Action Execution Before 400 Response". It is a flaw in
 * React Server Components mode: a router that runs server actions can be made to execute
 * one before the request is rejected.
 *
 * This console does not have that. It mounts <BrowserRouter> and renders <Routes>, with
 * no data router, no route `loader`/`action`, and no RSC — every write is a plain fetch
 * POST to a FastAPI route, where the auth gate and the safe-mode block live.
 *
 * DO NOT "fix" it by downgrading. npm offers 7.11.0, and that version is inside a
 * different range — 6.0.0–7.17.0 — carrying FOURTEEN advisories, several of which do
 * reach a plain client router:
 *
 *   GHSA-wrjc-x8rr-h8h6  open redirect via backslash in <Link> and useNavigate
 *   GHSA-2w69-qvjg-hvjx  XSS via open redirects
 *   GHSA-jjmj-jmhj-qwj2  open redirect leading to XSS
 *   GHSA-2j2x-hqr9-3h42  protocol-relative // open redirect
 *
 * We use <Link> and <NavLink> on every screen. 7.18.2 is the version that fixes all
 * of those, and npm's own advice FROM 7.11.0 is to install 7.18.2 — the two ranges
 * point at each other. Of the two, the one that leaves only an unreachable RSC flaw is
 * this one.
 *
 * So this test instead. The day someone adopts the data router or RSC, the exposure
 * becomes real and this fails, which is the moment to decide again.
 */

const SOURCE = readdirSync("src", { recursive: true, encoding: "utf-8" })
  .filter((name) => name.endsWith(".ts") || name.endsWith(".tsx"))
  .map((name) => readFileSync(`src/${name}`, "utf-8"))
  .join("\n");

describe("GHSA-qwww-vcr4-c8h2 does not reach this app", () => {
  it.each([
    ["createBrowserRouter", "the data router the advisory applies to"],
    ["createHashRouter", "same"],
    ["createMemoryRouter", "same"],
    ["RouterProvider", "mounts a data router"],
    ["useFetcher", "submits to a route action"],
    ["useSubmit", "submits to a route action"],
    ["<Form", "react-router's <Form> posts to a route action"],
  ])("does not use %s (%s)", (api) => {
    expect(SOURCE).not.toContain(api);
  });

  it("mounts the plain client router instead", () => {
    expect(SOURCE).toContain("<BrowserRouter");
  });
});
