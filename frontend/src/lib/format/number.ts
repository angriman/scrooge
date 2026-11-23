// Amounts, quantities and percentages as the app writes them: Swiss, `1’234.50`. The API sends
// each as exact decimal text (src/scrooge/web/wire.py); it is rounded here as text, never
// through a float, so a cent is never lost on the way. Each function writes as its namesake
// filter of the pages does (src/scrooge/web/app.py), in the Swiss way.

/** Between thousands: an apostrophe, as Swiss statements write them. */
export const GROUP = '’';
/** Before a negative figure: a minus, as wide as a plus, not a hyphen. */
export const MINUS = '−';

type Rounding = 'half-even' | 'half-up';

/** A decimal exactly: its magnitude is `units` / 10^`scale`. */
interface Exact {
	negative: boolean;
	units: bigint;
	scale: number;
}

const DECIMAL = /^([+-]?)(\d*)(?:\.(\d*))?$/;

function read(text: string): Exact {
	const found = DECIMAL.exec(text.trim());
	if (!found || (!found[2] && !found[3])) {
		throw new RangeError(`Not a decimal: ${JSON.stringify(text)}`);
	}
	const [, sign, whole, fraction = ''] = found;
	return { negative: sign === '-', units: BigInt((whole || '0') + fraction), scale: fraction.length };
}

/** `value` to `places` decimals. A tie goes to the even neighbour, as a Decimal formats, or
 * away from zero, as `ROUND_HALF_UP`. */
function round(value: Exact, places: number, rounding: Rounding): Exact {
	if (value.scale <= places) {
		const units = value.units * 10n ** BigInt(places - value.scale);
		return { negative: value.negative, units, scale: places };
	}
	const divisor = 10n ** BigInt(value.scale - places);
	let units = value.units / divisor;
	const twice = (value.units % divisor) * 2n;
	if (twice > divisor || (twice === divisor && (rounding === 'half-up' || units % 2n === 1n))) {
		units += 1n;
	}
	return { negative: value.negative, units, scale: places };
}

/** A tenth of a thousandth, a millionth…: `value` with its point moved `places` to the left. */
function shifted(value: Exact, places: number): Exact {
	return { ...value, scale: value.scale + places };
}

/** The digits of `value`, grouped, with a minus unless it is zero. */
function write(value: Exact, sign: 'minus' | 'always' = 'minus'): string {
	const digits = value.units.toString().padStart(value.scale + 1, '0');
	const point = digits.length - value.scale;
	const whole = digits.slice(0, point).replace(/\B(?=(\d{3})+(?!\d))/g, GROUP);
	const fraction = value.scale ? `.${digits.slice(point)}` : '';
	const zero = value.units === 0n;
	const lead = value.negative && !zero ? MINUS : sign === 'always' ? '+' : '';
	return lead + whole + fraction;
}

/** `1’234.50`: to read, two decimals (or `places`); the amount sent is not changed. */
export function money(text: string, places = 2): string {
	return write(round(read(text), places, 'half-even'));
}

/** `+1’234.50`: a gain or a loss, which its sign tells apart. */
export function signed(text: string, places = 2): string {
	return write(round(read(text), places, 'half-even'), 'always');
}

/** `850`, `12.3k`, `1.2M`: an amount at a glance, half up; the exact one is beside it. */
export function short(text: string): string {
	const value = read(text);
	const whole = round(value, 0, 'half-up');
	if (whole.units < 1000n) {
		return write(whole);
	}
	const thousands = round(shifted(value, 3), 1, 'half-up');
	if (thousands.units < 10_000n) {
		return `${write(thousands)}k`;
	}
	return `${write(round(shifted(value, 6), 1, 'half-up'))}M`;
}

/** `+12.3k`, `−850`: a change at a glance. */
export function shortSigned(text: string): string {
	const whole = round(read(text), 0, 'half-up');
	return !whole.negative && whole.units > 0n ? `+${short(text)}` : short(text);
}

/** `1’234.5`: a quantity or a price as it is stored, never rounded, without the zeros that say
 * nothing. */
export function exact(text: string): string {
	const written = write(read(text));
	return written.includes('.') ? written.replace(/\.?0+$/, '') : written;
}

/** `42.3%` of a share or a change sent in percent (`"42.33"`), to `places` decimals. */
export function percent(text: string, places = 1): string {
	return `${write(round(read(text), places, 'half-up'))}%`;
}

/** `+4.2%`, `−1.0%`: a change in percent. */
export function signedPercent(text: string, places = 1): string {
	return `${write(round(read(text), places, 'half-up'), 'always')}%`;
}

/** The sum of `values`, exactly, as decimal text. */
export function sum(values: readonly string[]): string {
	const read_ = values.map(read);
	const scale = Math.max(0, ...read_.map((value) => value.scale));
	const total = read_.reduce(
		(added, value) =>
			added + (value.negative ? -1n : 1n) * value.units * 10n ** BigInt(scale - value.scale),
		0n
	);
	const negative = total < 0n;
	const digits = (negative ? -total : total).toString().padStart(scale + 1, '0');
	const point = digits.length - scale;
	return `${negative ? '-' : ''}${digits.slice(0, point)}${scale ? `.${digits.slice(point)}` : ''}`;
}

/** `a` less `b`, exactly, as decimal text: how much a figure moved since it was `b`. */
export function difference(a: string, b: string): string {
	return sum([a, negate(b)]);
}

/** `value` with its sign turned. */
export function negate(value: string): string {
	const trimmed = value.trim();
	return trimmed.startsWith('-') ? trimmed.slice(1) : `-${trimmed.replace(/^\+/, '')}`;
}

/** `part` in percent of `whole`, exactly, to `places` decimals with a tie to the even
 * neighbour, as decimal text; null unless `whole` is above zero, when a percent says nothing. */
export function percentOf(part: string, whole: string, places = 2): string | null {
	const [top, bottom] = [read(part), read(whole)];
	if (bottom.negative || bottom.units === 0n) return null;
	// part / whole * 100, with `places` decimals: both made whole numbers of the same scale.
	const numerator = top.units * 10n ** BigInt(bottom.scale + places + 2);
	const denominator = bottom.units * 10n ** BigInt(top.scale);
	let units = numerator / denominator;
	const twice = (numerator % denominator) * 2n;
	if (twice > denominator || (twice === denominator && units % 2n === 1n)) units += 1n;
	const digits = units.toString().padStart(places + 1, '0');
	const point = digits.length - places;
	const sign = top.negative && units !== 0n ? '-' : '';
	return `${sign}${digits.slice(0, point)}${places ? `.${digits.slice(point)}` : ''}`;
}

/** The nearest float, for what is only drawn (the height of a bar, a point on a line), never
 * for what is written. */
export function approximate(text: string): number {
	read(text);
	return Number(text);
}
