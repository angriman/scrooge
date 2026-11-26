import { describe, expect, it as test } from 'vitest';
import { en } from './en.js';
import { it } from './it.js';
import { getLanguage, setLanguage, t } from './index.svelte.js';

describe('i18n', () => {
	test('switches between English and Italian and formats placeholders', () => {
		setLanguage('en');
		expect(getLanguage()).toBe('en');
		expect(t('nav.overview')).toBe('Overview');
		expect(t('nav.completeUpTo', { date: '30 Sep 2026' })).toBe('Complete up to 30 Sep 2026');

		setLanguage('it');
		expect(getLanguage()).toBe('it');
		expect(t('nav.overview')).toBe('Panoramica');
		expect(t('nav.completeUpTo', { date: '30 Set 2026' })).toBe('Completo fino al 30 Set 2026');

		setLanguage('en');
	});

	test('uses Payouts in English and Proventi in Italian for investments payouts', () => {
		setLanguage('en');
		expect(t('overview.incomeLink')).toBe('Payouts');
		expect(t('investments.viewIncome')).toBe('Payouts');
		expect(t('investments.pageTitlePayouts')).toBe('Payouts · scrooge');

		setLanguage('it');
		expect(t('overview.incomeLink')).toBe('Proventi');
		expect(t('investments.viewIncome')).toBe('Proventi');
		expect(t('investments.pageTitlePayouts')).toBe('Proventi · scrooge');

		setLanguage('en');
	});

	test('keeps English and Italian message keys and placeholders in sync', () => {
		expect(Object.keys(it).sort()).toEqual(Object.keys(en).sort());
		const placeholders = (text: string) => [...text.matchAll(/\{(\w+)\}/g)].map((m) => m[1]).sort();
		for (const key of Object.keys(en) as (keyof typeof en)[]) {
			expect(placeholders(it[key])).toEqual(placeholders(en[key]));
		}
	});
});
