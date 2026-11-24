// The merchants the Merchant chip offers: the known ones narrowed to what is typed, and the
// text typed itself when it names none of them. The names are the API's, as the merchant
// filter reads them.

import type { KnownMerchant } from '#lib/types.js';

/** At most this many merchants are listed at once: typing narrows the rest. */
export const LISTED = 50;

/** `text` as names are compared: its words, in lower case, accents and spaces left out where
 * they could differ. */
export function fold(text: string): string {
	return text
		.normalize('NFD')
		.replace(/\p{Diacritic}/gu, '')
		.toLowerCase()
		.split(/\s+/)
		.filter(Boolean)
		.join(' ');
}

/** The merchants whose name holds every word typed, as many as `most`: those whose name
 * starts with what is typed first, then those with a word that starts with its first word,
 * then the others, each in the order given (the largest first). Nothing typed: the first
 * `most`. */
export function narrow(
	merchants: readonly KnownMerchant[],
	typed: string,
	most = LISTED
): KnownMerchant[] {
	const wanted = fold(typed);
	if (!wanted) return merchants.slice(0, most);
	const words = wanted.split(' ');
	const ranked: KnownMerchant[][] = [[], [], []];
	for (const merchant of merchants) {
		const name = fold(merchant.name);
		if (!words.every((word) => name.includes(word))) continue;
		if (name.startsWith(wanted)) ranked[0].push(merchant);
		else if (name.split(' ').some((part) => part.startsWith(words[0]))) ranked[1].push(merchant);
		else ranked[2].push(merchant);
	}
	return ranked.flat().slice(0, most);
}

/** What is typed, offered as a merchant of its own: its spaces tidied, unless it is nothing or
 * already one of the merchants known. */
export function typedName(merchants: readonly KnownMerchant[], typed: string): string | null {
	const name = typed.trim().split(/\s+/).join(' ');
	if (!name) return null;
	const wanted = fold(name);
	return merchants.some((merchant) => fold(merchant.name) === wanted) ? null : name;
}
