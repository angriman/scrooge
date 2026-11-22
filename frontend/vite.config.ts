import adapter from '@sveltejs/adapter-static';
import { sveltekit } from '@sveltejs/kit/vite';
import { defineConfig } from 'vite';

// `scrooge serve` serves the build at /app (src/scrooge/web/app.py); `npm run dev` passes
// everything else to it, so the dev server reads the same API as the build.
const served = 'http://127.0.0.1:8765';

export default defineConfig({
	plugins: [
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
	}
});
