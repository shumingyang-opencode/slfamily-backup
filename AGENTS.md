# AGENTS.md — slfamily 搬家

Pixnet blog crawler project. Crawls `slfamily123.pixnet.net/blog` for migration.

## Project structure

```
crawler.py            # Main crawler script
output/               # All crawled data
├── all_posts.json    # Complete article index
├── index.md          # Article list in Markdown
└── posts/{id}_{slug}/
    ├── meta.json     # Title, date, category, URL
    ├── post.md       # Article content in Markdown (images ref local)
    ├── images.json   # Downloaded image manifest
    └── images/       # Original full-resolution images
```

## How to run

```bash
source .venv/bin/activate && python3 crawler.py
```

Dependencies in `.venv/` (created via `python3 -m venv .venv`):
`httpx`, `beautifulsoup4`, `markdownify`, `tqdm`.

Resume-safe: already-downloaded articles are skipped automatically.

## Key facts

- 345 articles, ~4,586 images, ~10 GB total
- Crawls 29 pagination pages → collects post URLs → fetches each article page
- Strips Pixnet thumbnail suffixes (`_n`, `_m`, `_t`, `_s`) from image URLs to get originals
- Skips Pixnet emoticons (`s.pixfs.net`) and Google Noto emoji (`fonts.gstatic.com`)
- Output is pure Markdown — ready for import into WordPress, Hugo, Ghost, etc.

## Deployment

- **GitHub Repo**: `shumingyang-opencode/slfamily-backup` (public)
- **GitHub Pages**: https://shumingyang-opencode.github.io/slfamily-backup/
- **Branch**: `main` (code + data + images via LFS), `gh-pages` (MkDocs-built HTML only)
- **Build locally**: rename `post.md` → `index.md` per post, then `mkdocs build`
- **Image URLs** in `gh-pages` point to `raw.githubusercontent.com` on the `main` branch (LFS-compatible)
- **MkDocs config**: `mkdocs.yml` with Material theme, `docs_dir: output`

### To redeploy

```bash
find output/posts -name "post.md" -exec sh -c 'mv "$1" "${1%/*}/index.md"' _ {} \;
sed -i '' 's|/post\\.md|/|g' output/index.md
source .venv/bin/activate && mkdocs build -d /tmp/mkdocs-site
# Fix image paths to raw.githubusercontent.com
find /tmp/mkdocs-site/posts -name "index.html" | while read f; do
  rel=$(python3 -c "import os; print(os.path.relpath(os.path.dirname('\$f'), '/tmp/mkdocs-site'))")
  python3 -c "
import re
with open('\$f') as fh:
    c = fh.read()
c = re.sub(r'src=\"(images/[^\"]+)\"', 'src=\"https://raw.githubusercontent.com/shumingyang-opencode/slfamily-backup/main/output/\$rel/' + r'\1\"', c)
with open('\$f', 'w') as fh:
    fh.write(c)
"
done
find /tmp/mkdocs-site -type f \\( -iname "*.jpg" -o -iname "*.jpeg" -o -iname "*.png" -o -iname "*.gif" -o -iname "*.webp" \\) -delete
cd /tmp/mkdocs-site && git init && git checkout -b gh-pages
git add -A && git commit -m "Deploy"
git remote add origin https://github.com/shumingyang-opencode/slfamily-backup.git
git push -f origin gh-pages
# Restore local files:
find output/posts -name "index.md" -exec sh -c 'mv "\$1" "\${1%/*}/post.md"' _ {} \;
sed -i '' 's|/)$|/post.md)|g' output/index.md
```
