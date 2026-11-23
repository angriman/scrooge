/// <reference types="vitest/config" />
/// <reference types="node" />
import adapter from '@sveltejs/adapter-static';
import { sveltekit } from '@sveltejs/kit/vite';
import tailwindcss from '@tailwindcss/vite';
import { defineConfig } from 'vite';

// `scrooge serve` serves the build at /app (src/scrooge/web/app.py); `npm run dev` passes
// everything else to it, so the dev server reads the same API as the build. SCROOGE_API
// names another one: `scrooge serve --port 8766` over the invented database of `make dev-db`.
const served = process.env.SCROOGE_API ?? 'http://127.0.0.1:8765';

export default defineConfig({
	plugins: [
		tailwindcss(),
		sveltekit({
			compilerOptions: {
				runes: ({ filename }) =>
					filename.split(/[/\\]/).includes('node_modules') ? undefined : true
			},
			// One page for every address: the data comes from /api once the page is open.
			adapter: adapter({ pages: '../src/scrooge/web/spa', fallback: 'index.html' }),
			paths: { base: '/app' }
		})
	],
	server: {
		proxy: Object.fromEntries(['/api', '/logos', '/login'].map((path) => [path, served]))
	},
	test: {
		include: ['src/**/*.test.ts']
	}
});
