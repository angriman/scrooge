// Days as the app writes them. The API sends a day as ISO text (`2026-09-30`); it is read
// here as the day it names, never as an instant, so no time zone moves it.

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const ISO_DAY = /^(\d{4})-(\d{2})-(\d{2})$/;

function parts(text: string): [year: number, month: number, day: number] {
	const found = ISO_DAY.exec(text);
	if (!found) {
		throw new RangeError(`Not a day: ${JSON.stringify(text)}`);
	}
	return [Number(found[1]), Number(found[2]), Number(found[3])];
}

/** `30 Sep 2026`. */
export function day(text: string): string {
	const [year, month, date] = parts(text);
	return `${date} ${MONTHS[month - 1]} ${year}`;
}

/** `30 Sep`, for a day whose year goes without saying. */
export function dayOfYear(text: string): string {
	const [, month, date] = parts(text);
	return `${date} ${MONTHS[month - 1]}`;
}

/** `Sep 2026`. */
export function month(text: string): string {
	const [year, month] = parts(text);
	return `${MONTHS[month - 1]} ${year}`;
}

/** The day `days` after `text` (before it, when negative), as ISO text. */
export function addDays(text: string, days: number): string {
	const [year, month, date] = parts(text);
	return new Date(Date.UTC(year, month - 1, date + days)).toISOString().slice(0, 10);
}
