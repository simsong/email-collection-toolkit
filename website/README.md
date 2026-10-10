<!-- Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved. -->

# Email Collection Toolkit website

This is a Zola site using the local `envelope-rainbow` theme. The checked-in
site is built by `.github/workflows/pages.yml` and deployed with GitHub Pages
Actions from `main`; there is no `gh-pages` branch. After the first merge,
select **Settings → Pages → Build and deployment → Source: GitHub Actions**.

The workflow resolves canonical stable, alpha and beta release assets into
`data/releases.toml` during the build.
The committed data file is the no-release fallback used for local previews.
The primary download button suggests macOS or Windows from browser platform
hints, with an adjacent **Show all installers** link and explicit platform links.
The update-stream preference belongs in the installed app's Preferences panel,
where Sparkle and WinSparkle use the saved release/preview channel. The website
has no stream selector or browser-local update setting. Unknown platforms,
missing assets and disabled JavaScript retain the generic downloads page.
Run `make website-download-check` for actual Zola rendering and headless browser
acceptance; `make test-website-navigation` checks responsive layout.

Describe BagIt/Mailbag as the native email archive storage format, never as a
separate export. Record website changes in `content/changelog.md`, linked from
`content/about.md`; application release notes remain separate. About also
provides the author biography and links to the author and project on GitHub.

Zola `0.23.4` is pinned in the workflow. To preview locally, install that
version and run `make website-preview`. The temporary preview is served only on
`127.0.0.1:1111`; override the port with `WEBSITE_PREVIEW_PORT=...`. Stop it with
Control-C. Generated files stay in `.tmp/website-preview`; Zola reuses this
disposable directory when restarting or rebuilding the preview. This does not publish
the site.
