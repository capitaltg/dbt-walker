# Vendored assets

`mermaid.min.js` — **mermaid 11.4.1**, from
`https://cdn.jsdelivr.net/npm/mermaid@11.4.1/dist/mermaid.min.js`
(sha256 starts `a43bc1afd446f9c4`, ~2.5 MB). MIT-licensed — the license text
is redistributed alongside as `mermaid.LICENSE` and ships in the wheel.

Vendored deliberately (design decision L2): the generated lineage app inlines
this so it renders with **zero network** — corporate proxies and VPNs commonly
block CDNs, and the app ships to work laptops.

The bundle ends with `globalThis.mermaid = ...`, so inlining it in a plain
`<script>` tag exposes the `mermaid` global (no ESM import needed).

To upgrade: download the new pinned version to this path, update the version
and hash above, and re-run the test suite (the app's graph rendering is
covered).
