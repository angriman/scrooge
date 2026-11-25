import { en, type MessageKey, type Messages } from './en.js';
import { it } from './it.js';

export type Language = 'en' | 'it';

export const LANGUAGES: readonly Language[] = ['en', 'it'];

const MESSAGES: Record<Language, Messages> = { en, it };

let current = $state<Language>('en');

export function getLanguage(): Language {
	return current;
}

export function setLanguage(language: Language): void {
	current = language;
	if (typeof document !== 'undefined') {
		document.documentElement.lang = language;
	}
}

export function t(key: MessageKey, vars?: Record<string, string | number>): string {
	const template = MESSAGES[current][key];
	if (!vars) return template;
	return template.replace(/\{(\w+)\}/g, (_, name: string) =>
		name in vars ? String(vars[name]) : `{${name}}`
	);
}

export type { MessageKey, Messages };
