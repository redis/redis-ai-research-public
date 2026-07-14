# Talk slides — Spec-Driven Agents for RQE

Single Markdown source that generates both the HTML deck and the PDF via [Marp](https://marp.app/).

## Files

- `talk-slides.md` — the slide source (edit this)
- `rqe-dark-theme.css` — Marp theme (colors, cards, callouts, bar charts)
- `build.sh` — regenerates `talk-slides.html` and `talk-slides.pdf`
- `build_pdf.py` — legacy reportlab PDF generator (superseded by the Marp workflow)

## Workflow

```bash
./build.sh          # writes talk-slides.html + talk-slides.pdf
```

Live preview while editing: install the **Marp for VS Code** extension and open
`talk-slides.md` — the preview pane renders with the theme as you type
(set `markdown.marp.themes` to include `./rqe-dark-theme.css` in workspace settings).

## Editing notes

- Slides are separated by `---`; the `######` heading renders as the red uppercase kicker.
- Plain Markdown works everywhere (lists, tables, code fences).
- Reusable HTML classes from the theme: `card`, `cols`/`cols3`, `callout`, `flow`,
  `bars`/`bar-row`, `big-stat`, and color helpers `muted/accent/blue/good/warn`.
- The architecture and timeline diagrams are inline SVG — edit coordinates/text directly.
