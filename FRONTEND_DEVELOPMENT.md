# Frontend Development Guide

Your PKM Bridge Server now has a modern frontend built with **Astro + Tailwind CSS + TypeScript**.

## Project Structure

```
frontend/
├── src/
│   ├── components/          # Reusable UI components
│   │   ├── LoginForm.astro  # Login screen
│   │   └── ChatInterface.astro  # Main chat UI
│   ├── layouts/
│   │   └── Layout.astro     # Base HTML layout
│   ├── pages/
│   │   └── index.astro      # Main page with TypeScript logic
│   └── styles/
│       └── global.css       # Tailwind CSS imports
├── astro.config.mjs         # Astro configuration
├── tailwind.config.js       # Tailwind configuration
├── tsconfig.json            # TypeScript configuration
└── package.json             # Dependencies and scripts

templates/                   # Build output (Flask serves this)
└── index.html              # Generated from Astro build
```

## Development Workflow

### Dual Server Setup (Standard)

**Run two servers during development:**

#### Terminal 1: Flask Backend

```bash
./pkm-bridge-server.py
```

This runs on **http://localhost:8000** and handles:
- API endpoints (`/query`, `/login`, etc.)
- Authentication
- Claude API calls
- Tool execution

#### Terminal 2: Astro Frontend

```bash
cd frontend
bun run dev
```

This runs on **http://localhost:4321** and provides:
- ✨ Instant hot module replacement (HMR)
- 🔄 Auto-reload on file changes
- 🚀 Fast development experience
- 🔌 API proxying to Flask backend

**During development, visit http://localhost:4321**

### How API Proxying Works

The Astro dev server automatically proxies all API calls to Flask:

```
Browser → http://localhost:4321/query
   ↓ (Astro proxies)
Backend → http://localhost:8000/query
```

**Benefits:**
- No CORS issues
- Seamless development
- Same URLs as production

**Configured in** `frontend/astro.config.mjs`:
```js
server: {
  proxy: {
    '/query': 'http://localhost:8000',
    '/login': 'http://localhost:8000',
    // etc.
  }
}
```

## Making Changes

### Modifying Components

**Example: Change login page styling**

Edit `frontend/src/components/LoginForm.astro`:

```astro
<div class="bg-white p-8 rounded-xl shadow-2xl w-full max-w-md">
  <!-- Change to larger width: -->
  <div class="bg-white p-8 rounded-xl shadow-2xl w-full max-w-lg">
```

**Save** → Browser reloads instantly ✨

### Adding New Components

Create a new component:

```astro
<!-- frontend/src/components/MyNewComponent.astro -->
---
interface Props {
  title: string;
}
const { title } = Astro.props;
---

<div class="p-4 bg-blue-100 rounded">
  <h2 class="text-xl font-bold">{title}</h2>
  <slot />
</div>
```

Use it in a page:

```astro
---
import MyNewComponent from '../components/MyNewComponent.astro';
---

<MyNewComponent title="Hello">
  <p>Content goes here</p>
</MyNewComponent>
```

### Styling with Tailwind

**No more CSS files!** Use utility classes:

```astro
<!-- Instead of writing CSS: -->
<style>
  .my-button {
    background-color: blue;
    padding: 1rem;
    border-radius: 0.5rem;
  }
</style>

<!-- Use Tailwind classes: -->
<button class="bg-blue-500 p-4 rounded-lg hover:bg-blue-600">
  Click me
</button>
```

**Common patterns:**

- **Layout**: `flex flex-col gap-4`
- **Spacing**: `p-4 m-2 px-6 py-3`
- **Colors**: `bg-blue-500 text-white border-gray-300`
- **Rounded**: `rounded rounded-lg rounded-xl`
- **Shadows**: `shadow shadow-md shadow-lg`
- **Hover**: `hover:bg-blue-600 hover:shadow-lg`
- **Responsive**: `sm:text-base md:text-lg lg:text-xl`

**Learn more:** https://tailwindcss.com/docs

### TypeScript Logic

Client-side TypeScript lives in `<script>` tags in `.astro` files:

```astro
<script>
  // TypeScript is fully supported!
  const element = document.getElementById('my-button') as HTMLButtonElement;

  element.addEventListener('click', (e: MouseEvent) => {
    console.log('Clicked!', e);
  });
</script>
```

**Features:**
- Full TypeScript support
- Type checking
- Auto-completion in VS Code
- Strict mode enabled

## Building for Production

### Build the Frontend

```bash
cd frontend
bun run build
```

**What happens:**
1. Astro compiles all components
2. Tailwind purges unused CSS
3. TypeScript is compiled to JavaScript
4. Output goes to `../templates/index.html`
5. Assets go to `../templates/_astro/`

### Run Production Build

```bash
# Build frontend
cd frontend && bun run build

# Return to root and start Flask
cd .. && ./pkm-bridge-server.py
```

**Visit http://localhost:8000** - Flask serves the built files.

## Commands Reference

| Command | Description |
|---------|-------------|
| `bun run dev` | Start Astro dev server (port 4321) |
| `bun run build` | Build for production → `../templates/` |
| `bun run preview` | Preview production build locally |

## Troubleshooting

### "Cannot find module" errors

Make sure you're in the `frontend/` directory:

```bash
cd frontend
bun install
```

### Changes not showing up

**In development:**
- Check you're visiting **http://localhost:4321** (not 8000)
- Hard refresh: `Cmd+Shift+R` (Mac) or `Ctrl+Shift+R` (Win/Linux)

**In production:**
- Did you run `bun run build`?
- Did you restart Flask?

### API calls failing in development

**Check Flask is running:**
```bash
curl http://localhost:8000/health
```

**Check Astro proxy config:**
```bash
cat frontend/astro.config.mjs | grep proxy
```

### TypeScript errors

Run the type checker:
```bash
cd frontend
bun run astro check
```

## Best Practices

### 1. Component Organization

**Keep components small and focused:**

```astro
<!-- ✅ Good: Focused component -->
<MessageBubble message={msg} />

<!-- ❌ Bad: Too much in one component -->
<EntireApp />
```

### 2. Tailwind Classes

**Use logical grouping:**

```astro
<!-- ✅ Good: Grouped by purpose -->
<div class="
  flex items-center gap-2
  px-4 py-3
  bg-blue-500 text-white
  rounded-lg shadow-md
  hover:bg-blue-600
">

<!-- ❌ Bad: Random order -->
<div class="hover:bg-blue-600 flex rounded-lg px-4 bg-blue-500 gap-2 items-center py-3 shadow-md text-white">
```

### 3. TypeScript Safety

**Always type your variables:**

```ts
// ✅ Good
const input = document.getElementById('input') as HTMLInputElement;
const messages: Message[] = [];

// ❌ Bad
const input = document.getElementById('input');
const messages = [];
```

### 4. Scoped Styles

If you need custom CSS, scope it to the component:

```astro
<div class="custom-gradient">
  Content
</div>

<style>
  /* Only affects this component */
  .custom-gradient {
    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  }
</style>
```

## Migration from Old HTML

The old `templates/index.html` has been replaced by the Astro build output.

**What changed:**
- ✅ 400+ lines of inline CSS → Tailwind utility classes
- ✅ Messy `<style>` block → Component-scoped styles
- ✅ One huge file → Modular components
- ✅ Plain JS → TypeScript with types
- ✅ No build step → Modern build with HMR

**What stayed the same:**
- ✅ All functionality preserved
- ✅ Same authentication flow
- ✅ Same API endpoints
- ✅ Flask still serves the HTML

## Further Reading

- **Astro Docs**: https://docs.astro.build
- **Tailwind CSS**: https://tailwindcss.com/docs
- **TypeScript**: https://www.typescriptlang.org/docs

## Tips for Development

### VS Code Extensions

Install these for best experience:
- **Astro** (astro-build.astro-vscode)
- **Tailwind CSS IntelliSense** (bradlc.vscode-tailwindcss)
- **TypeScript** (Built-in, just enable)

### Quick Reference

**Add a new color:**
```js
// frontend/tailwind.config.js
export default {
  theme: {
    extend: {
      colors: {
        'brand': '#007aff',
      }
    }
  }
}
```

Then use: `class="bg-brand"`

**Add custom fonts:**
```css
/* frontend/src/styles/global.css */
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700&display=swap');

@layer base {
  body {
    font-family: 'Inter', sans-serif;
  }
}
```

## Questions?

The setup is standard Astro + Tailwind - any tutorial or documentation for these tools applies to this project!

Happy coding! 🚀
