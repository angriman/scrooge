// What a broker's row of the accounts page is booked as elsewhere: one account per currency.

/** The names a row's account is booked under elsewhere (Transactions, Income, rules), said
 * once: `Booked as ACME CHF, ACME EUR and ACME USD.` Empty for an account booked under its own
 * name alone. */
export function bookedAs(parts: readonly string[]): string {
	if (parts.length < 2) return '';
	return `Booked as ${parts.slice(0, -1).join(', ')} and ${parts[parts.length - 1]}.`;
}
