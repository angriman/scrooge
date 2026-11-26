// Client-side theme preference (System / Dark / Light) and live OS prefers-color-scheme sync.
// Stored in localStorage under THEME_STORAGE_KEY so it works in read-only mode too.

export const THEME_STORAGE_KEY = 'scrooge-theme';
export const LIGHT_MEDIA_QUERY = '(prefers-color-scheme: light)';

export type ThemePreference = 'system' | 'dark' | 'light';
export type ResolvedTheme = 'night' | 'alpine';

let preference = $state<ThemePreference>('system');
let activeTheme = $state<ResolvedTheme>('night');

export function parseThemePreference(value: string | null | undefined): ThemePreference {
	if (value === 'dark' || value === 'light' || value === 'system') return value;
	return 'system';
}

export function resolveTheme(
	preferenceValue: ThemePreference,
	prefersLight: boolean
): ResolvedTheme {
	if (preferenceValue === 'dark') return 'night';
	if (preferenceValue === 'light') return 'alpine';
	return prefersLight ? 'alpine' : 'night';
}

function defaultStorage(): Storage | null {
	try {
		return typeof localStorage !== 'undefined' ? localStorage : null;
	} catch {
		return null;
	}
}

export function readStoredPreference(
	storage: Pick<Storage, 'getItem'> | null | undefined = defaultStorage()
): ThemePreference {
	if (!storage) return 'system';
	try {
		return parseThemePreference(storage.getItem(THEME_STORAGE_KEY));
	} catch {
		return 'system';
	}
}

export function writeStoredPreference(
	next: ThemePreference,
	storage: Pick<Storage, 'setItem'> | null | undefined = defaultStorage()
): void {
	if (!storage) return;
	try {
		storage.setItem(THEME_STORAGE_KEY, next);
	} catch {
		// Ignore blocked or full storage; the preference still applies for this session.
	}
}

type MatchMediaFn = (query: string) => MediaQueryList;

function defaultMatchMedia(): MatchMediaFn | null {
	return typeof window !== 'undefined' && typeof window.matchMedia === 'function'
		? window.matchMedia.bind(window)
		: null;
}

export function systemPrefersLight(
	matchMediaFn: MatchMediaFn | null | undefined = defaultMatchMedia()
): boolean {
	if (!matchMediaFn) return false;
	try {
		return matchMediaFn(LIGHT_MEDIA_QUERY).matches;
	} catch {
		return false;
	}
}

function defaultRoot(): HTMLElement | null {
	return typeof document !== 'undefined' ? document.documentElement : null;
}

export function getThemePreference(): ThemePreference {
	return preference;
}

export function getResolvedTheme(): ResolvedTheme {
	return activeTheme;
}

export function applyTheme(
	preferenceValue: ThemePreference,
	prefersLight: boolean,
	root: HTMLElement | null | undefined = defaultRoot()
): ResolvedTheme {
	const resolved = resolveTheme(preferenceValue, prefersLight);
	activeTheme = resolved;
	if (root) {
		root.dataset.theme = resolved;
	}
	return resolved;
}

export function setThemePreference(
	next: ThemePreference,
	options?: {
		storage?: Pick<Storage, 'setItem'> | null;
		matchMedia?: MatchMediaFn | null;
		root?: HTMLElement | null;
	}
): ResolvedTheme {
	const parsed = parseThemePreference(next);
	preference = parsed;
	writeStoredPreference(
		parsed,
		options && 'storage' in options ? options.storage : defaultStorage()
	);
	const prefersLight = systemPrefersLight(
		options && 'matchMedia' in options ? options.matchMedia : defaultMatchMedia()
	);
	return applyTheme(
		parsed,
		prefersLight,
		options && 'root' in options ? options.root : defaultRoot()
	);
}

export function initTheme(options?: {
	storage?: Pick<Storage, 'getItem'> | null;
	matchMedia?: MatchMediaFn | null;
	root?: HTMLElement | null;
}): () => void {
	const storage = options && 'storage' in options ? options.storage : defaultStorage();
	const matchMediaFn =
		options && 'matchMedia' in options ? options.matchMedia : defaultMatchMedia();
	const root = options && 'root' in options ? options.root : defaultRoot();

	const initial = readStoredPreference(storage);
	preference = initial;
	const mql = matchMediaFn ? matchMediaFn(LIGHT_MEDIA_QUERY) : null;
	applyTheme(initial, mql?.matches ?? false, root);

	if (!mql || typeof mql.addEventListener !== 'function') {
		return () => {};
	}

	const onChange = (event: MediaQueryListEvent) => {
		if (preference === 'system') {
			applyTheme('system', event.matches, root);
		}
	};

	mql.addEventListener('change', onChange);
	return () => mql.removeEventListener('change', onChange);
}
