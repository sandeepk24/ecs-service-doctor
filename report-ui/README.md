# ECS Report UI

React + Vite frontend for the shareable HTML report.

## Develop

```bash
npm install
npm run dev
```

Open http://localhost:5173 — uses built-in sample data when no report JSON is injected.

## Build template

```bash
npm run build
```

Outputs:

- `dist/index.html` — single-file bundle (JS + CSS inlined)
- `report.template.html` — copied for `ecs_doctor.py` to inject live report data

After changing styles or components, rebuild and regenerate the README sample:

```bash
npm run build
cd ..
python examples/generate_sample_html.py
```

## Stack

- React 19
- Vite 6 + `vite-plugin-singlefile`
- Fonts: Plus Jakarta Sans, DM Sans, JetBrains Mono
