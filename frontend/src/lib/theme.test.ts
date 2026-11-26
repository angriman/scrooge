import { describe, expect, it } from 'vitest';
import {
	LIGHT_MEDIA_QUERY,
	THEME_STORAGE_KEY,
	applyTheme,
	getResolvedTheme,
	getThemePreference,
	initTheme,
	parseThemePreference,
	readStoredPreference,
	resolveTheme,
	setThemePreference,
	writeStoredPreference
} from './theme.svelte.js';

function fakeRoot(): HTMLElement {
	return { dataset: {} } as unknown as HTMLElement;
}

function fakeStorage(initial: Record<string, string> = {}) {
	const data = new Map(Object.entries(initial));
	return {
		getItem: (key: string) => data.get(key) ?? null,
		setItem: (key: string, value: string) => {
			data.set(key, value);
		}
	};
}

function fakeMatchMedia(initialMatches: boolean) {
	let matches = initialMatches;
	const listeners = new Set<(event: MediaQueryListEvent) => void>();
	const mql = {
		media: LIGHT_MEDIA_QUERY,
		get matches() {
			return matches;
		},
		addEventListener: (_type: string, listener: (event: MediaQueryListEvent) => void) => {
			listeners.add(listener);
		},
		removeEventListener: (_type: string, listener: (event: MediaQueryListEvent) => void) => {
			listeners.delete(listener);
		}
	} as unknown as MediaQueryList;

	return {
		matchMedia: () => mql,
		trigger: (next: boolean) => {
			matches = next;
			for (const listener of listeners) {
				listener({ matches: next, media: LIGHT_MEDIA_QUERY } as MediaQueryListEvent);
			}
		},
		listenerCount: () => listeners.size
	};
}

describe('theme', () => {
	it('parses valid preferences and falls back to system', () => {
		expect(parseThemePreference('system')).toBe('system');
		expect(parseThemePreference('dark')).toBe('dark');
		expect(parseThemePreference('light')).toBe('light');
		expect(parseThemePreference(null)).toBe('system');
		expect(parseThemePreference(undefined)).toBe('system');
		expect(parseThemePreference('alpine')).toBe('system');
		expect(parseThemePreference('night')).toBe('system');
	});

	it('resolves system, dark, and light against prefers-color-scheme: light', () => {
		expect(resolveTheme('system', true)).toBe('alpine');
		expect(resolveTheme('system', false)).toBe('night');
		expect(resolveTheme('dark', true)).toBe('night');
		expect(resolveTheme('dark', false)).toBe('night');
		expect(resolveTheme('light', false)).toBe('alpine');
		expect(resolveTheme('light', true)).toBe('alpine');
	});

	it('handles missing, corrupt, and throwing storage gracefully', () => {
		expect(readStoredPreference(null)).toBe('system');
		expect(readStoredPreference(fakeStorage({ [THEME_STORAGE_KEY]: 'light' }))).toBe('light');
		expect(readStoredPreference(fakeStorage({ [THEME_STORAGE_KEY]: 'garbage' }))).toBe('system');

		const broken = {
			getItem: () => {
				throw new Error('SecurityError');
			},
			setItem: () => {
				throw new Error('SecurityError');
			}
		};
		expect(readStoredPreference(broken)).toBe('system');
		expect(() => writeStoredPreference('dark', broken)).not.toThrow();
	});

	it('applies theme to document root and persists choices', () => {
		const root = fakeRoot();
		const storage = fakeStorage();
		const media = fakeMatchMedia(true);

		expect(applyTheme('light', false, root)).toBe('alpine');
		expect(root.dataset.theme).toBe('alpine');
		expect(getResolvedTheme()).toBe('alpine');

		expect(setThemePreference('dark', { storage, matchMedia: media.matchMedia, root })).toBe(
			'night'
		);
		expect(getThemePreference()).toBe('dark');
		expect(getResolvedTheme()).toBe('night');
		expect(root.dataset.theme).toBe('night');
		expect(storage.getItem(THEME_STORAGE_KEY)).toBe('dark');

		expect(setThemePreference('system', { storage, matchMedia: media.matchMedia, root })).toBe(
			'alpine'
		);
		expect(root.dataset.theme).toBe('alpine');
		expect(storage.getItem(THEME_STORAGE_KEY)).toBe('system');
	});

	it('reacts to OS appearance changes only when preference is system', () => {
		const root = fakeRoot();
		const storage = fakeStorage();
		const media = fakeMatchMedia(false);

		const stop = initTheme({ storage, matchMedia: media.matchMedia, root });
		expect(getThemePreference()).toBe('system');
		expect(root.dataset.theme).toBe('night');
		expect(media.listenerCount()).toBe(1);

		media.trigger(true);
		expect(root.dataset.theme).toBe('alpine');
		expect(getResolvedTheme()).toBe('alpine');

		media.trigger(false);
		expect(root.dataset.theme).toBe('night');

		// Explicit choice ignores live OS changes.
		setThemePreference('light', { storage, matchMedia: media.matchMedia, root });
		expect(root.dataset.theme).toBe('alpine');
		media.trigger(false);
		expect(root.dataset.theme).toBe('alpine');

		// Switching back to system resumes following live OS changes.
		setThemePreference('system', { storage, matchMedia: media.matchMedia, root });
		expect(root.dataset.theme).toBe('night');
		media.trigger(true);
		expect(root.dataset.theme).toBe('alpine');

		stop();
		expect(media.listenerCount()).toBe(0);
	});

	it('keeps an explicit preference in memory when storage throws', () => {
		const root = fakeRoot();
		const blockedStorage = {
			getItem: () => {
				throw new Error('SecurityError');
			},
			setItem: () => {
				throw new Error('SecurityError');
			}
		};
		const media = fakeMatchMedia(false);
		const stop = initTheme({ storage: blockedStorage, matchMedia: media.matchMedia, root });
		expect(getThemePreference()).toBe('system');

		setThemePreference('light', { storage: blockedStorage, matchMedia: media.matchMedia, root });
		expect(getThemePreference()).toBe('light');
		expect(root.dataset.theme).toBe('alpine');
		stop();
	});
});
