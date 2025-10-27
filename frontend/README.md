# Frontend - Astro + Tailwind CSS

Modern, component-based frontend for the PKM Bridge Server.

## Quick Start

### Development (with hot reload)

```bash
# Terminal 1: Start Flask backend
cd ..
./pkm-bridge-server.py

# Terminal 2: Start Astro dev server
bun run dev
```

Visit **http://localhost:4321** (Astro dev server with instant hot reload)

### Production Build

```bash
bun run build
```

Outputs to `../templates/index.html` (Flask serves this)

## Commands

| Command | Description |
|---------|-------------|
| `bun run dev` | Start dev server on port 4321 with HMR |
| `bun run build` | Build production files to `../templates/` |
| `bun run preview` | Preview production build locally |

## Project Structure

```
src/
├── components/       # Reusable UI components
│   ├── LoginForm.astro
│   └── ChatInterface.astro
├── layouts/          # Page layouts
│   └── Layout.astro
├── pages/            # Routes (index.astro = /)
│   └── index.astro
└── styles/           # Global styles
    └── global.css
```

## Tech Stack

- **Astro 5** - Modern static site framework with HMR
- **Tailwind CSS v4** - Utility-first CSS framework
- **TypeScript** - Type-safe JavaScript (strict mode)
- **Bun** - Fast package manager & runtime

## Development Workflow

1. **Start both servers** (Flask backend + Astro frontend)
2. **Visit http://localhost:4321** during development
3. **Edit any `.astro` file** → Browser auto-reloads instantly ✨
4. **API calls are proxied** to Flask automatically

## API Proxying

The dev server automatically proxies API calls to Flask backend:

```
Browser request:  http://localhost:4321/query
     ↓ (proxied)
Flask backend:    http://localhost:8000/query
```

Configured in `astro.config.mjs` - no CORS issues!

## Build Output

Production build creates:
- `../templates/index.html` - Minified HTML file
- `../templates/_astro/` - CSS & JS assets (minified, hashed)

Flask serves these files in production mode.

## Making Changes

### Edit Components

```bash
# Edit any component
vim src/components/ChatInterface.astro

# Save → Browser reloads automatically
```

### Add Tailwind Classes

```astro
<!-- Change colors, spacing, etc. -->
<button class="bg-blue-500 hover:bg-blue-600 px-4 py-2 rounded">
  <!-- Changes appear instantly -->
</button>
```

### Modify Logic

```astro
<script>
  // TypeScript is fully supported
  const button = document.getElementById('btn') as HTMLButtonElement;

  button.addEventListener('click', () => {
    console.log('Clicked!');
  });
</script>
```

## Documentation

See **`../FRONTEND_DEVELOPMENT.md`** for complete guide including:
- Component development
- Tailwind CSS usage
- TypeScript patterns
- Best practices
- Troubleshooting

## Learn More

- [Astro Docs](https://docs.astro.build)
- [Tailwind CSS Docs](https://tailwindcss.com/docs)
- [TypeScript Handbook](https://www.typescriptlang.org/docs)
