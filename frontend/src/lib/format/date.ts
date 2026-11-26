// Days as the app writes them. The API sends a day as ISO text (`2026-09-30`); it is read
// here as the day it names, never as an instant, so no time zone moves it.

import { getLanguage, type Language } from '#lib/i18n/index.svelte.js';

const MONTHS: Record<Language, readonly string[]> = {
	en: ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'],
	it: ['Gen', 'Feb', 'Mar', 'Apr', 'Mag', 'Giu', 'Lug', 'Ago', 'Set', 'Ott', 'Nov', 'Dic']
};
const MONTH_NAMES: Record<Language, readonly string[]> = {
	en: [
		'January',
		'February',
		'March',
		'April',
		'May',
		'June',
		'July',
		'August',
		'September',
		'October',
		'November',
		'December'
	],
	it: [
		'Gennaio',
		'Febbraio',
		'Marzo',
		'Aprile',
		'Maggio',
		'Giugno',
		'Luglio',
		'Agosto',
		'Settembre',
		'Ottobre',
		'Novembre',
		'Dicembre'
	]
};
const WEEKDAYS: Record<Language, readonly string[]> = {
	en: ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'],
	it: ['Dom', 'Lun', 'Mar', 'Mer', 'Gio', 'Ven', 'Sab']
};
const ISO_DAY = /^(\d{4})-(\d{2})-(\d{2})$/;

function parts(text: string): [year: number, month: number, day: number] {
	const found = ISO_DAY.exec(text);
	if (!found) {
		throw new RangeError(`Not a day: ${JSON.stringify(text)}`);
	}
	return [Number(found[1]), Number(found[2]), Number(found[3])];
}

/** `30 Sep 2026`. */
export function day(text: string, language: Language = getLanguage()): string {
	const [year, month, date] = parts(text);
	return `${date} ${MONTHS[language][month - 1]} ${year}`;
}

/** `30 Sep`, for a day whose year goes without saying. */
export function dayOfYear(text: string, language: Language = getLanguage()): string {
	const [, month, date] = parts(text);
	return `${date} ${MONTHS[language][month - 1]}`;
}

/** `Tue 16 Sep`, and its year when it is not `thisYear`: a day in a list of days. */
export function weekday(
	text: string,
	thisYear: number,
	language: Language = getLanguage()
): string {
	const [year, month, date] = parts(text);
	const name = WEEKDAYS[language][new Date(Date.UTC(year, month - 1, date)).getUTCDay()];
	return `${name} ${date} ${MONTHS[language][month - 1]}${year === thisYear ? '' : ` ${year}`}`;
}

/** `Sep 2026`. */
export function month(text: string, language: Language = getLanguage()): string {
	const [year, month] = parts(text);
	return `${MONTHS[language][month - 1]} ${year}`;
}

/** The midnight that starts the day `text` names, where the reader is: what a chart's time
 * scale places, and ticks on, in local time. */
export function toDate(text: string): Date {
	const [year, month, date] = parts(text);
	return new Date(year, month - 1, date);
}

/** `Sep 2026`, of a local midnight such as a chart's tick. */
export function monthOf(date: Date, language: Language = getLanguage()): string {
	return `${MONTHS[language][date.getMonth()]} ${date.getFullYear()}`;
}

/** `Sep`, of a local midnight such as a timeline tick whose year is shown on January. */
export function shortMonthOf(date: Date, language: Language = getLanguage()): string {
	return MONTHS[language][date.getMonth()];
}

/** `5 Nov`, of a local midnight such as a chart's tick. */
export function dayOf(date: Date, language: Language = getLanguage()): string {
	return `${date.getDate()} ${MONTHS[language][date.getMonth()]}`;
}

/** The day `days` after `text` (before it, when negative), as ISO text. */
export function addDays(text: string, days: number): string {
	const [year, month, date] = parts(text);
	return new Date(Date.UTC(year, month - 1, date + days)).toISOString().slice(0, 10);
}

/** `October 2026`, of a month written `2026-10`. */
export function monthName(text: string, language: Language = getLanguage()): string {
	const [year, month] = parts(`${text}-01`);
	return `${MONTH_NAMES[language][month - 1]} ${year}`;
}

/** How many days from `first` to `last`, both ISO days: 1 from one day to the next. */
export function daysBetween(first: string, last: string): number {
	const [a, b] = [parts(first), parts(last)];
	return Math.round((Date.UTC(b[0], b[1] - 1, b[2]) - Date.UTC(a[0], a[1] - 1, a[2])) / 86_400_000);
}

/** Today in the reader's time zone, as `YYYY-MM-DD`. */
export function todayIso(now: Date = new Date()): string {
	return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`;
}
